import sys
import raft_pb2 as pb2
import raft_pb2_grpc as pb2_grpc
import grpc
import threading, time
from threading import Thread
import random
from concurrent import futures

CONFIG_PATH = "config.conf"  # overwrite this with your config file path
SERVERS = {}
TERM, VOTED, STATE, VOTES = 0, False, "Follower", 0
LEADER_ID = None
IN_ELECTIONS = False
# Time limit in nano seconds.
TIME_LIMIT = (random.randrange(150, 301) / 1000)
SERVER_ID = int(sys.argv[1])
VOTED_NODE = -1
# Latest time is the timer. Some processes may restart this timer.
LATEST_TIMER = -1
CANDIDATE_THREADS = []
LEADER_THREADS = []


# Handler for RPC functions.
class RaftHandler(pb2_grpc.RaftServiceServicer):
    # This function is called by the Candidate during the elections to collect votes.
    def RequestVote(self, request, context):
        global TERM, VOTED, STATE, VOTED_NODE, IN_ELECTIONS, LATEST_TIMER
        candidate_term, candidate_id = request.term, request.candidateId
        result = False
        IN_ELECTIONS = True
        if TERM < candidate_term:
            TERM = candidate_term
            result = True
            VOTED = True
            VOTED_NODE = candidate_id
            print(f"Voted for node {candidate_id}.")
        reset_timer(leader_died, TIME_LIMIT)
        # LATEST_TIME = time.time_ns()
        reply = {"term": TERM, "result": result}
        return pb2.RequestVoteResponse(**reply)

    # In this lab, this function is only used to send heartbeat messages and confirm leader is alive.
    def AppendEntries(self, request, context):
        global TERM, STATE, LEADER_ID, VOTED, VOTED_NODE, LATEST_TIMER
        leader_term, leader_id = request.term, request.leaderId
        result = False
        if leader_term >= TERM:
            LEADER_ID = leader_id
            result = True
            if leader_term > TERM:
                VOTED = False
                VOTED_NODE = -1
                TERM = leader_term
        reset_timer(leader_died, TIME_LIMIT)
        # LATEST_TIME = time.time_ns()
        reply = {"term": TERM, "result": result}
        return pb2.AppendEntriesResponse(**reply)

    # This function is called from the client to get leader.
    def GetLeader(self, request, context):
        global IN_ELECTIONS, VOTED, VOTED_NODE, LEADER_ID
        print("Command from client: getleader")
        if IN_ELECTIONS and not VOTED:
            print("None None")
            return pb2.GetLeaderResponse(**{"leaderId": -1, "leaderAddress": "-1"})

        if IN_ELECTIONS:
            print(f"{VOTED_NODE} {SERVERS[VOTED_NODE]}")
            return pb2.GetLeaderResponse(**{"leaderId": VOTED_NODE, "leaderAddress": SERVERS[VOTED_NODE]})

        return pb2.GetLeaderResponse(**{"leaderId": LEADER_ID, "leaderAddress": SERVERS[LEADER_ID]})

    # This function is called from client to suspend server for PERIOD seconds.
    def Suspend(self, request, context):
        SUSPEND_PERIOD = int(request.period)
        print(f"Command from client: suspend {SUSPEND_PERIOD}")
        print(f"Sleeping for {SUSPEND_PERIOD} seconds")
        time.sleep(SUSPEND_PERIOD)
        return pb2.SuspendResponse(**{})


# Read config file to make the list of servers IDs and addresses.
def read_config(path):
    global SERVERS
    with open(path) as configFile:
        lines = configFile.readlines()
        for line in lines:
            parts = line.split()
            SERVERS[int(parts[0])] = (f"{str(parts[1])}:{str(parts[2])}")


# Utility function used by running follower behaviour to ensure that leader sends us a message within the timelimit.
# def get_messages():
#     global IN_ELECTIONS, TIME_LIMIT
#     if (LEADER_ID is not None):
#         channel = grpc.insecure_channel(SERVERS[LEADER_ID])
#         stub = pb2_grpc.RaftServiceStub(channel)
#     IN_ELECTIONS = False


# Runs the behaviour of changing from a follower.
# declares leader dead, and becomes a candidate.
def leader_died():
    global STATE
    if STATE != "Follower":
        return
    print("The leader is dead")
    STATE = "Candidate"
    run_candidate()


def get_vote(server):
    global TERM, STATE, TIME_LIMIT, VOTES
    try:
        print("I'm trying to get votes from server x")
        channel = grpc.insecure_channel(server)
        stub = pb2_grpc.RaftServiceStub(channel)
        request = pb2.RequestVoteMessage(**{"term": TERM, "candidateId": SERVER_ID})
        response = stub.RequestVote(request)
        if response.term > TERM:
            TERM = response.term
            STATE = "Follower"
            TIME_LIMIT = (random.randrange(100, 301) / 10000)
            reset_timer(leader_died, TIME_LIMIT)
        if response.result:
            VOTES += 1
        return response.result
    except grpc.RpcError:
        print("Getting votes from server X failed")
        pass


def process_votes():
    global STATE, LEADER_ID, CANDIDATE_THREADS, TIME_LIMIT
    for thread in CANDIDATE_THREADS:
        thread.join(0)
    print("Votes received")
    if VOTES > len(SERVERS) / 2:
        print(f"I am a leader. Term: {TERM}")
        STATE = "Leader"
        LEADER_ID = SERVER_ID
        # reset_timer(leader_died, TIME_LIMIT)
        run_leader()
        # LATEST_TIME = time.time_ns()
    else:
        STATE = "Follower"
        TIME_LIMIT = (random.randrange(100, 301) / 10000)
        reset_timer(leader_died, TIME_LIMIT)


# Runs the behaviour of a candidate that reaches out to alive servers and asks them to vote to itself.
# If it gets the majority of votes, it becomes a leader, else, it's downgraded to a follower and runs follower behaviour.
def run_candidate():
    global TERM, STATE, LEADER_ID, LATEST_TIMER, TIME_LIMIT, IN_ELECTIONS, VOTED_NODE
    global SERVER_ID, VOTES, CANDIDATE_THREADS
    TERM += 1
    IN_ELECTIONS = True
    VOTED_NODE = SERVER_ID
    CANDIDATE_THREADS = []
    VOTES = 1
    print(f"I'm a candidate. Term: {TERM}.\nVoted for node {SERVER_ID}")
    # Requesting votes.
    for key, value in SERVERS.items():
        if SERVER_ID is key:
            continue
        CANDIDATE_THREADS.append(Thread(target=get_vote, kwargs={'server':value}))
    print("I made candidate threads")
    # Check if you won the election and can become a leader.
    for thread in CANDIDATE_THREADS:
        thread.start()
    print("I started candidate threads")
    reset_timer(process_votes, TIME_LIMIT)
    print("Candidate stuff finished")


# Utility function used by the leader to send a heartbeat to all alive servers.
def send_heartbeat(server):
    global STATE, TERM
    if STATE != "Leader":
        return
    try:
        channel = grpc.insecure_channel(server)
        stub = pb2_grpc.RaftServiceStub(channel)
        request = pb2.AppendEntriesMessage(**{"term": TERM, "leaderId": SERVER_ID})
        response = stub.AppendEntries(request)
        if response.term > TERM:
            STATE = "Follower"
            TERM = response.term
    except grpc.RpcError:
        print("Sending heartbeats to server X failed")


def send_heartbeats():
    global LEADER_THREADS
    for thread in LEADER_THREADS:
        thread.start()


# Runs the behaviour of a leader that sends a heartbeat message after 50 milliseconds.
def run_leader():
    global SERVER_ID, STATE, LEADER_THREADS
    # Send a heartbeat after 50 milliseconds.
    LEADER_THREADS = []
    for key in SERVERS:
        if SERVER_ID is key:
            continue
        LEADER_THREADS.append(Thread(target=send_heartbeat, kwargs={'server':SERVERS[key]}))
    reset_timer(send_heartbeats, 0.05)


def reset_timer(func, time_limit):
    global LATEST_TIMER
    LATEST_TIMER.cancel()
    LATEST_TIMER = threading.Timer(time_limit, func)
    LATEST_TIMER.start()
    # print("Timer is reset")


# Initializes the server and runs a loop to call the corresponding responsible function of its current state:
# Leader, Candidate, or Follower. Every server starts as a follower.
def run_server():
    global TERM, STATE, SERVERS, TIME_LIMIT, LATEST_TIMER
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb2_grpc.add_RaftServiceServicer_to_server(RaftHandler(), server)
    server.add_insecure_port(SERVERS[SERVER_ID])
    print(TIME_LIMIT)
    # print follower start
    print(f"I'm a follower. Term: {TERM}")
    LATEST_TIMER = threading.Timer(TIME_LIMIT, leader_died)
    LATEST_TIMER.start()
    try:
        server.start()
        while (True):
            server.wait_for_termination()
    except KeyboardInterrupt:
        print(f"Server {SERVER_ID} is shutting down")

def printthread(tr):
    print(tr)


if __name__ == '__main__':
    read_config(CONFIG_PATH)
    run_server()

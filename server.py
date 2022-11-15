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
TERM, VOTED, STATE = 0, False, "Follower"
LEADER_ID = None
IN_ELECTIONS = False
# Time limit in nano seconds.
TIME_LIMIT = (random.randrange(150, 301)*1000000)
SERVER_ID = int(sys.argv[1])
VOTED_NODE = -1
# Latest time is the time the timer got started. Some process may restart this timer.
LATEST_TIME = time.time_ns()

# Handler for RPC functions.
class RaftHandler(pb2_grpc.RaftServiceServicer):
    
    # This function is called by the Candidate during the elections to collect votes.
    def RequestVote(self, request, context):
        global TERM, VOTED, STATE, VOTED_NODE, IN_ELECTIONS, LATEST_TIME
        candidate_term, candidate_id = request.term, request.candidateId
        result = False
        IN_ELECTIONS = True
        if TERM < candidate_term:
            TERM = candidate_term
            result = True
            VOTED = True
            VOTED_NODE = candidate_id
            print(f"Voted for node {candidate_id}.")
        LATEST_TIME = time.time_ns()
        reply = {"term": TERM, "result": result}
        return pb2.RequestVoteResponse(**reply)
    
    # In this lab, this function is only used to send heartbeat messages and confirm leader is alive.
    def AppendEntries(self, request, context):
        global TERM, STATE, LEADER_ID, VOTED, VOTED_NODE, LATEST_TIME
        leader_term, leader_id = request.term, request.leaderId
        result = False
        if leader_term >= TERM:
            LEADER_ID = leader_id
            result = True
            if leader_term > TERM:
                VOTED = False
                VOTED_NODE = -1
                TERM = leader_term
        LATEST_TIME = time.time_ns()
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
            return pb2.GetLeaderResponse(**{"leaderId":VOTED_NODE, "leaderAddress":SERVERS[VOTED_NODE]})

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
            SERVERS[int(parts[0])] = f"{parts[1]}:{parts[2]}"
            
# Utility function used by run_follower to ensure that leader sends us a message within the timelimit.
def get_messages():
    global IN_ELECTIONS, TIME_LIMIT, LATEST_TIME
    if(LEADER_ID is not None):
        channel = grpc.insecure_channel(SERVERS[LEADER_ID])
        stub = pb2_grpc.RaftServiceStub(channel)
    IN_ELECTIONS = False
    while True:
        # Check that timer is not timed out.
        if (time.time_ns() - LATEST_TIME) >= TIME_LIMIT:
            return False
        # As long as it's not, and the timer's getting reset in time, we're good.
        pass
    
# Runs the behaviour of a follower that receives messages from leader until it times out,
# declares leader dead, and becomes a candidate.
def run_follower():
    global STATE, IN_ELECTIONS
    message_received = get_messages()
    if not message_received:
        print("The leader is dead")
        STATE = "Candidate"
        
# Runs the behaviour of a candidate that reaches out to alive servers and asks them to vote to itself.
# If it gets the majority of votes, it becomes a leader, else, it's downgraded to a follower and runs follower behaviour.
def run_candidate():
    global TERM, STATE, LEADER_ID, LATEST_TIME, TIME_LIMIT, IN_ELECTIONS, VOTED_NODE, SERVER_ID
    LATEST_TIME = time.time_ns()
    TERM += 1
    IN_ELECTIONS = True
    VOTED_NODE = SERVER_ID
    votes = 1  # voted for itself
    print(f"I'm a candidate. Term: {TERM}.\nVoted for node {SERVER_ID}")
    # Requesting votes.
    for key in SERVERS:
        try:
            if SERVER_ID is key:
                continue
            channel = grpc.insecure_channel(SERVERS[key])
            stub = pb2_grpc.RaftServiceStub(channel)
            request =  pb2.RequestVoteMessage(**{"term": TERM, "candidateId": SERVER_ID})
            response = stub.RequestVote(request)
            if response.result:
                votes += 1
            # If timer expires, become follower.
            if response.term > TERM or (time.time_ns() - LATEST_TIME) >= TIME_LIMIT:
                TERM = response.term
                STATE = "Follower"
                LATEST_TIME = time.time_ns()
                TIME_LIMIT = (random.randrange(100, 301)*1000000)
                break
        except grpc.RpcError:
            continue
        # Check if you won the election and can become a leader.
        print("Votes received")
        if votes > len(SERVERS) / 2:
                print(f"I am a leader. Term: {TERM}")
                STATE = "Leader"
                LEADER_ID = SERVER_ID
                LATEST_TIME = time.time_ns()
                break
        else:
                STATE = "Follower"

# Utility function used by the leader to send a heartbeat to all alive servers.
def send_heartbeats():
    global STATE, TERM

    if STATE != "Leader":
        return
    for key in SERVERS:
        if SERVER_ID is key:
            continue
        try:
            channel = grpc.insecure_channel(SERVERS[key])
            stub = pb2_grpc.RaftServiceStub(channel)
            request = pb2.AppendEntriesMessage(**{"term": TERM, "leaderId": SERVER_ID})
            response = stub.AppendEntries(request)
            if response.term > TERM:
                STATE = "Follower"
                TERM = response.term
        except grpc.RpcError:
            continue
            
# Runs the behaviour of a leader that sends a heartbeat message after 50 milliseconds.
def run_leader():
    global SERVER_ID
    # Send a heartbeat after 50 milliseconds.
    send_messages = threading.Timer(0.05,send_heartbeats())
    send_messages.start()
    send_messages.cancel()
    
# Initializes the server and runs a loop to call the corresponding responsible function of its current state:
# Leader, Candidate, or Follower. Every server starts as a follower.
def run_server():
    global TERM, STATE, SERVERS
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb2_grpc.add_RaftServiceServicer_to_server(RaftHandler(), server)
    server.add_insecure_port(SERVERS[SERVER_ID])
    server.start()
    print(f"I'm a follower. Term: {TERM}")
    try:
        while True:
            if STATE == "Follower":
                  run_follower()
            elif STATE == "Candidate":
                  run_candidate()
            elif STATE == "Leader":
                  run_leader()
    except KeyboardInterrupt:
        print(f"Server {SERVER_ID} is shutting down")


if __name__ == '__main__':
    read_config(CONFIG_PATH)
    run_server()

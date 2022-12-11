import sys
import raft_pb2 as pb2
import raft_pb2_grpc as pb2_grpc
import grpc
import threading, time
from threading import Thread
import random
from concurrent import futures

# Config file has servers addresses that'll be stored in servers.
CONFIG_PATH = "config.conf"  # overwrite this with your config file path
SERVERS = {}

# Server identifying variables.
TERM, VOTED, STATE, VOTES = 0, False, "Follower", 0
LEADER_ID = None
IN_ELECTIONS = False
SERVER_ID = int(sys.argv[1])
VOTED_NODE = -1
SERVER_ACTIVE = True
# Time limit in seconds for timouts, and timer to set the time limit for certain functionalities.
TIME_LIMIT = (random.randrange(150, 301) / 1000)
LATEST_TIMER = -1

# Threads used to request votes from servers as a candidate, and to append entries as a leader.
CANDIDATE_THREADS = []
LEADER_THREADS = []

# Commit Index is index of last log entry on server
# Last applied is index of last applied log entry on server
commitIndex, lastApplied, lastLogTerm = 0, 0, 0
# For applied commits.
ENTRIES = {}
# For non-applied commits.
LOGS = []

# NextIndex: list of indices of next log entry to send to server
# MatchIndex: list of indices of latest log entry known to be on every server
nextIndex, matchIndex = [], []
matchTerm = []
n_logs_replicated = 1
# TODO: handle when server is suspended and can't respond to requests
# Handler for RPC functions.
class RaftHandler(pb2_grpc.RaftServiceServicer):

    # This function is called by the Candidate during the elections to collect votes.
    def RequestVote(self, request, context):
        global TERM, VOTED, STATE, VOTED_NODE, IN_ELECTIONS, LATEST_TIMER
        global commitIndex, lastLogTerm
        candidate_term, candidate_id = request.term, request.candidateId
        candidateLastLogIndex, candidateLastLogTerm = request.lastLogIndex, request.lastLogTerm

        result = False
        IN_ELECTIONS = True
        if TERM < candidate_term:
            TERM = candidate_term
            result = True
            VOTED = True
            VOTED_NODE = candidate_id
            print(f"Voted for node {candidate_id}.")
            run_follower()
        elif TERM == candidate_term:
            if VOTED or candidateLastLogIndex < len(LOGS) or STATE != "Follower":
                pass
            elif (candidateLastLogIndex == len(LOGS) and (LOGS[candidateLastLogIndex - 1]["TERM"] != candidateLastLogTerm)):
                pass
            else:
                result = True
                VOTED = True
                VOTED_NODE = candidate_id
                print(f"Voted for node {candidate_id}.")
                reset_timer(leader_died, TIME_LIMIT)
        else:
            if STATE == "Follower":
                reset_timer(leader_died, TIME_LIMIT)

        reply = {"term": TERM, "result": result}
        return pb2.RequestVoteResponse(**reply)

    # This function is used by leader to append entries in followers.
    def AppendEntries(self, request, context):
        global TERM, STATE, LEADER_ID, VOTED, VOTED_NODE
        global LATEST_TIMER, commitIndex, ENTRIES, lastApplied
        leader_term, leader_id = request.term, request.leaderId
        prevLogIndex, prevLogTerm = request.prevLogIndex, request.prevLogTerm
        entries, leaderCommit = request.entries, request.leaderCommit
        #TODO: you may need to spell out what's in entries
        result = False
        if leader_term >= TERM:
            # Leader is already in a different term than mine.
            if leader_term > TERM:
                VOTED = False
                VOTED_NODE = -1
                TERM = leader_term
                LEADER_ID = leader_id
                run_follower()

            if prevLogIndex <= len(LOGS):
                result = True
                if len(entries) > 0:
                    LOGS.append({"TERM": leader_term, "ENTRY": entries})

                if leaderCommit > commitIndex:
                    commitIndex = min(leaderCommit, len(LOGS))
                    while commitIndex > lastApplied:
                        key, value = LOGS[lastApplied]["ENTRY"]["key"], LOGS[lastApplied]["ENTRY"]["value"]
                        ENTRIES[key] = value
                        lastApplied += 1


            reset_timer(leader_died, TIME_LIMIT)
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
        global SERVER_ACTIVE
        SUSPEND_PERIOD = int(request.period)
        print(f"Command from client: suspend {SUSPEND_PERIOD}")
        print(f"Sleeping for {SUSPEND_PERIOD} seconds")
        SERVER_ACTIVE = False
        time.sleep(SUSPEND_PERIOD)
        reset_timer(run_server_role(), SUSPEND_PERIOD)
        return pb2.SuspendResponse(**{})

    # This function is called from client to add key, value/append entry to servers.
    def SetVal(self, request, context):
        key, val = request.key, request.val
        global STATE, SERVERS, LEADER_ID, LOGS, TERM, nextIndex, matchIndex
        if STATE == "Follower":
            try:
                channel = grpc.insecure_channel(SERVERS[LEADER_ID])
                stub = pb2_grpc.RaftServiceStub(channel)
                request = pb2.SetValMessage(**{"key": key, "val": val})
                response = stub.SetVal(request)
                return response
            except grpc.RpcError:
                return pb2.SetValResponse(**{"success": False})
        elif STATE == "Candidate":
            return pb2.SetValResponse(**{"success": False})
        else:
            nextIndex.append(len(LOGS))
            LOGS.append({"TERM": TERM, "ENTRY": request})
            return pb2.SetValResponse(**{"success": True})
            #return pb2.SetValResponse(**{"success": False})

    # This function is called from client to get value associated with key.
    def GetVal(self, request, context):
        key = request.key
        global ENTRIES
        if key in ENTRIES:
            val = ENTRIES[key]
            return pb2.GetValResponse(**{"success": True, "value": val})
        return pb2.GetValResponse(**{"success": False})


# Read config file to make the list of servers IDs and addresses.
def read_config(path):
    global SERVERS
    with open(path) as configFile:
        lines = configFile.readlines()
        for line in lines:
            parts = line.split()
            SERVERS[int(parts[0])] = (f"{str(parts[1])}:{str(parts[2])}")


# Runs the behaviour of changing from a follower:
# declares leader dead and becomes a candidate.
def leader_died():
    global STATE
    if STATE != "Follower":
        return
    print("The leader is dead")
    STATE = "Candidate"
    run_candidate()

def run_follower():
    global STATE
    STATE = "Follower"
    print(f"I'm a follower. Term: {TERM}")
    reset_timer(leader_died, TIME_LIMIT)

def get_vote(server):
    global TERM, STATE, TIME_LIMIT, VOTES
    try:
        #print("I'm trying to get votes from server x")
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
    # Check if you won the election and can become a leader.
    for thread in CANDIDATE_THREADS:
        thread.start()
    reset_timer(process_votes, TIME_LIMIT)


def replicate_log(server):
    global LOGS, STATE, matchIndex, matchTerm, nextIndex, n_logs_replicated
    log = LOGS[nextIndex]
    prevLogIndex = matchIndex[(len(matchIndex) -1)]
    prevLogTerm = LOGS[prevLogIndex]["TERM"]
    leaderCommit = nextIndex[0]
    try:
        channel = grpc.insecure_channel(server)
        stub = pb2_grpc.RaftServiceStub(channel)
        request = pb2.AppendEntriesMessage(**{"term": TERM, "leaderId": SERVER_ID,
                                              "prevLogIndex": prevLogIndex, "prevLogTerm": prevLogTerm,
                                              "entries": log,"leaderCommit": leaderCommit})
        response = stub.AppendEntries(request)
        if(response.term > TERM):
            STATE = "Follower"
            reset_timer(leader_died, TIME_LIMIT)
            run_follower()
        if response.result:
            n_logs_replicated += 1
    except grpc.RpcError:
        pass

# Utility function used by the leader to send a heartbeat to all alive servers.
def send_heartbeat(server):
    global LOGS, STATE, matchIndex, nextIndex, n_logs_replicated
    log = {}
    prevLogIndex = matchIndex[(len(matchIndex) - 1)]
    prevLogTerm = LOGS[prevLogIndex]["TERM"]
    leaderCommit = matchIndex[(len(matchIndex) - 1)]
    try:
        channel = grpc.insecure_channel(server)
        stub = pb2_grpc.RaftServiceStub(channel)
        request = pb2.AppendEntriesMessage(**{"term": TERM, "leaderId": SERVER_ID,
                                              "prevLogIndex": prevLogIndex, "prevLogTerm": prevLogTerm,
                                              "entries": log, "leaderCommit": leaderCommit})
        response = stub.AppendEntries(request)
        if (response.term > TERM):
            STATE = "Follower"
            reset_timer(leader_died, TIME_LIMIT)
            run_follower()
        if response.result:
            n_logs_replicated += 1
    except grpc.RpcError:
        pass

# Utility function used to send heartbeats or replicate logs to every server.
def send_heartbeats():
    global LEADER_THREADS
    for thread in LEADER_THREADS:
        thread.start()
    reset_timer(verify_replicated_logs, TIME_LIMIT)

# Runs the behaviour of a leader that sends a heartbeat message after 50 milliseconds.
def run_leader():
    global SERVER_ID, STATE, LEADER_THREADS, n_logs_replicated, nextIndex
    # Send messages after 50 milliseconds.
    LEADER_THREADS = []
    n_logs_replicated = 1
    if len(nextIndex) > 0:
        for key in SERVERS:
            if SERVER_ID is key:
                continue
            LEADER_THREADS.append(Thread(target=replicate_log, kwargs={'server':SERVERS[key]}))
    else:
        for key in SERVERS:
            if SERVER_ID is key:
                continue
            LEADER_THREADS.append(Thread(target=send_heartbeat, kwargs={'server':SERVERS[key]}))
    reset_timer(send_heartbeats, 0.05)

def verify_replicated_logs():

    global LEADER_THREADS, matchIndex, nextIndex
    for thread in LEADER_THREADS:
        thread.join(0)
    if (n_logs_replicated == len(SERVERS)):
        matchIndex.append(nextIndex[0])
        nextIndex.pop(0)
    else:
        run_leader()
def reset_timer(func, time_limit):
    global LATEST_TIMER
    LATEST_TIMER.cancel()
    LATEST_TIMER = threading.Timer(time_limit, func)
    LATEST_TIMER.start()

def run_server_role():
    global SERVER_ACTIVE
    SERVER_ACTIVE = True
    if(STATE == "Leader"):
        run_leader()
    elif STATE == "Candidate":
        run_candidate()
    else:
        run_follower()
# Initializes the server. Every server starts as a follower.
def run_server():
    global TERM, STATE, SERVERS, TIME_LIMIT, LATEST_TIMER
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb2_grpc.add_RaftServiceServicer_to_server(RaftHandler(), server)
    server.add_insecure_port(SERVERS[SERVER_ID])
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


if __name__ == '__main__':
    read_config(CONFIG_PATH)
    run_server()

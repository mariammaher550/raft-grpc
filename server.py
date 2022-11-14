import sys
import raft_pb2 as pb2
import raft_pb2_grpc as pb2_grpc
import grpc
import threading, time
from multiprocessing import Queue
from threading import Thread
from queue import Empty
import os
import random
import socket
from concurrent import futures

CONFIG_PATH = "config.conf"  # overwrite this with your own config file path
SERVERS = {}
TERM, VOTED, STATE = 0, False, "Follower"
LEADER_ID = None
IN_ELECTIONS = False  # will be true if it was suspended
START_SUSPEND = None  # time suspend started
TIME_LIMIT = (random.randrange(150, 301)*1000000)
SERVER_ID = sys.argv[1]
VOTED_NODE = -1
LATEST_TIME = time.time_ns()

class RaftHandler(pb2_grpc.RaftServiceServicer):
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
            print(f"Voted for {candidate_id}.")
        LATEST_TIME = time.time_ns()
        reply = {"term": TERM, "result": result}
        return pb2.RequestVoteResponse(**reply)

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

    def GetLeader(self, request, context):
        global IN_ELECTIONS, VOTED, VOTED_NODE, LEADER_ID

        if IN_ELECTIONS and not VOTED:
            return pb2.GetLeaderResponse(**{"id": None, "address": None})

        if IN_ELECTIONS:
            return pb2.GetLeaderResponse(VOTED_NODE)

        return pb2.GetLeaderResponse(**{"id": LEADER_ID, "address": SERVERS[LEADER_ID]})

    def Suspend(self, request, context):
        SUSPEND_PERIOD = int(request.period)
        print(f"Sleeping for {SUSPEND_PERIOD} seconds.")
        time.sleep(SUSPEND_PERIOD)
        return pb2.SuspendResponse()


def read_config(path):
    global SERVERS
    with open(path) as configFile:
        lines = configFile.readlines()
        for line in lines:
            parts = line.split()
            SERVERS[parts[0]] = f"{parts[1]}:{parts[2]}"

def get_messages():
    global IN_ELECTIONS, TIME_LIMIT, LATEST_TIME
    if(LEADER_ID is not None):
        channel = grpc.insecure_channel(SERVERS[LEADER_ID])
        stub = pb2_grpc.RaftServiceStub(channel)
    IN_ELECTIONS = False
    while True:
        if (time.time_ns() - LATEST_TIME) >= TIME_LIMIT:
        # check that timer is not timed out
            return False
        # as long as it's not, and the timer's getting reset in time, we're good
        pass
def run_follower():
    global STATE, IN_ELECTIONS
    message_received = get_messages()
    if not message_received:
        print("The leader is dead")
        STATE = "Candidate"
def run_candidate():
    global TERM, STATE, LEADER_ID, LATEST_TIME, TIME_LIMIT
    LATEST_TIME = time.time_ns()
    # requesting votes
    TERM += 1
    votes = 1  # voted for itself
    print(f"I'm a candidate. Term: {TERM}.\nVoted for node {SERVER_ID}")
    for key in SERVERS:
        if SERVER_ID is key:
            continue
        channel = grpc.insecure_channel(SERVERS[key])
        stub = pb2_grpc.RaftServiceStub(channel)
        response = stub.RequestVote(**{"term": TERM, "candidateId": SERVER_ID})
        if response.result:
            votes += 1

        if votes > len(SERVERS) / 2:
            STATE = "Leader"
            LEADER_ID = SERVER_ID
            LATEST_TIME = time.time_ns()
            break
        else:
            # temp value that can be replaced later
            STATE = "Follower"

        # if timer expires, also become follower
        if response.term > TERM or (time.time_ns() - LATEST_TIME) >= TIME_LIMIT:
            TERM = response.term
            STATE = "Follower"
            LATEST_TIME = time.time_ns()
            TIME_LIMIT = (random.randrange(150, 301)*1000000)
            break

def send_heartbeats():
    global STATE, TERM

    if STATE != "Leader":
        return
    for key in SERVERS:
        if SERVER_ID is key:
            continue
        channel = grpc.insecure_channel(SERVERS[key])
        stub = pb2_grpc.RaftServiceStub(channel)
        response = stub.AppendEnteriesMessage(**{"term": TERM, "leaderId": SERVER_ID})
        if response.term > TERM:
            STATE = "Follower"
            TERM = response.term
def run_leader():
    # send a heartbeat every 50 milliseconds
    # @TODO make sure leaders get cancelledddd
    send_messages = threading.Timer(0.05,send_heartbeats())
    send_messages.start()
    if STATE != "Leader":
        send_messages.cancel()

def run_server():

    global TERM, STATE, SERVERS
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb2_grpc.add_RaftServiceServicer_to_server(RaftHandler, server)
    server.add_insecure_port(SERVERS[SERVER_ID])
    server.start()
    print(f"I'm a follower. Term: {TERM}")
    try:
        #server.wait_for_termination()
        while True:
            if STATE == "Follower":
                    run_follower()
            elif STATE == "Candidate":
                    run_candidate()
            elif STATE == "Leader":
                    run_leader()
    except KeyboardInterrupt:
        print(f"Server {SERVER_ID} is shutting down")


def driver():
    read_config(CONFIG_PATH)
    run_server()

if __name__ == '__main__':
    driver()

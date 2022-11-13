import sys

import grpc

import raft_pb2 as pb2
import raft_pb2_grpc as pb2_grpc
import time
from multiprocessing import Queue
from threading import Thread
from queue import Empty
import os
import random
import socket
from concurrent import futures

CONFIG_PATH = "config.conf"  # overwrite this with your own config file path
SERVERS = {}
TERM, VOTED, STATE = 0, False, "follower"
LEADER_ID = None
IN_ELECTIONS = False  # will be true if it was suspended
START_SUSPEND = None  # time suspend started
TIME_LIMIT = random.randrange(150, 301)
SERVER_ID = sys.argv[1]
VOTED_NODE = {"id": None, "address": None}


def change_to_follower():
    pass


def change_to_leader():
    pass


def change_to_candidate():
    pass




class RaftHandler(pb2_grpc.RaftServiceServicer):
    def RequestVote(self, request, context):
        global TERM, VOTED, STATE, VOTED_NODE
        candidate_id, candidate_term = request.id, request.term
        result = False

        if TERM < candidate_term:
            TERM = candidate_term
            VOTED = False
            VOTED_NODE["id"], VOTED_NODE["address"] = None, None

        if not VOTED and candidate_term == TERM:
            result = True
            VOTED = True

        if result and STATE in ["leader", "candidate"]:
            STATE = "follower"

        reply = {"term": TERM, "result": result}
        return pb2.RequestResponse(**reply)

    def AppendEntries(self, request, context):
        global TERM, STATE, LEADER_ID, VOTED

        leader_term, leader_id = request.term, request.id

        success = False
        if leader_term >= TERM:
            LEADER_ID = leader_id
            success = True
            if leader_term > TERM:
                VOTED = False
                TERM = leader_term

        reply = {"term": TERM, "result": success}
        return pb2.RequestResponse(**reply)

    def GetLeader(self, request, context):
        global IN_ELECTIONS, VOTED, VOTED_NODE, LEADER_ID

        if IN_ELECTIONS and not VOTED:
            return pb2.CurrentLeader({"id": None, "address": None})

        if IN_ELECTIONS:
            return pb2.CurrentLeader(VOTED_NODE)

        return pb2.CurrentLeader({"id": LEADER_ID, "address": SERVERS[LEADER_ID]})

    def Suspend(self, request, context):
        # @todo how to actually suspend?
        global IN_ELECTIONS
        IN_ELECTIONS = True
        START_SUSPEND = int(request.period)
        # sleep?
        return pb2.Empty()


def read_config(path):
    global SERVERS
    with open(path) as configFile:
        lines = configFile.readline()
        for line in lines:
            parts = line.split()
            SERVERS[parts[0]] = f"{parts[1]}:{parts[2]}"


def start_server():
    global TERM, STATE, SERVERS
    server = grpc.Server(futures.ThreadPoolExecutor(max_workers=10))
    pb2_grpc.add_RaftServiceServicer_to_server(RaftHandler, server)
    server.add_insecure_port(SERVERS[SERVER_ID])
    server.start()

    try:
        server.wait_for_termination()
        while True:
            match STATE:
                case "Candidate":
                    # TERM += 1
                    flag = True
                    # @todo reset timer

                    # requesting votes
                    votes = 1  # voted for itself
                    print(f"Voted for node {SERVER_ID}")
                    for key in SERVERS:
                        if SERVER_ID is key:
                            continue
                        channel = grpc.insecure_channel(SERVERS[key])
                        stub = pb2_grpc.RaftServiceStub(channel)
                        response = stub.RequestVote({"term": TERM, "id": SERVER_ID})
                        if response.term > TERM:
                            TERM = response.term
                            STATE = "Follower"
                            break
                        if response.result:
                            votes += 1
                    if votes > len(SERVERS) / 2:
                        STATE = "Leader"
                    else:
                        STATE = "Follower"

                case "Leader":
                    # sending heartbeat
                    # @todo every 50 secs
                    for key in SERVERS:
                        if SERVER_ID is key:
                            continue
                        channel = grpc.insecure_channel(SERVERS[key])
                        stub = pb2_grpc.RaftServiceStub(channel)
                        response = stub.AppendEnteries({"term": TERM, "id": SERVER_ID})
                        if response.term > TERM:
                            STATE = "Follower"
                            TERM = response.term






    except KeyboardInterrupt:
        print(f"Server {SERVER_ID} is shutting down")


def driver():
    read_config(CONFIG_PATH)
    start_server()


if __name__ == '__main__':
    driver()

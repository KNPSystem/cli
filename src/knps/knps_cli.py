import hashlib
from pathlib import Path
import argparse
import configparser
from datetime import date
from datetime import datetime
from datetime import timezone
import json
import distutils.util
import os
import requests
import json
import csv
import PyPDF2
import re
import codecs
import random
import time
import webbrowser
import yaml
import subprocess
import uuid
import socket
import threading
import psutil
import mimetypes
from binaryornot.check import is_binary

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import logging

import pkg_resources

from knps.settings import (
    CACHE_FILE_PROCESSING,
    KNPS_SERVER_DEV,
    KNPS_SERVER_PROD
)

CFG_DIR = '.knps'
CFG_FILE = 'knps.cfg'

DB_FILE = '.knpsdb'
DIR_DB_FILE = '.knps_dir_db'

PROCESS_SYNC_AGE_SECONDS = 10

###################################################
# Some util functions
###################################################
def get_version(file_loc = __file__):
    cwdir = os.path.dirname(os.path.realpath(file_loc))

    proj_ver = subprocess.run(["git", "describe", "--tags", "--long"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=cwdir).stdout.strip()
    if proj_ver:
        rev_count = subprocess.run(["git", "log", "--oneline"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=cwdir).stdout.strip()
        rev_count = len(rev_count.split("\n"))
        return f'{proj_ver}-{rev_count}-development'
    else:
        proj_ver = pkg_resources.require('knps-cli')[0].version
        return f'v{proj_ver}-release'



HASH_CACHE = {}
def hash_file(fname, stats=None):
    if not stats:
        try:
            p = Path(fname)
            stats = p.stat()
            key = f'{fname}-{stats.st_mtime}'

            if key not in HASH_CACHE:
                hash_md5 = hashlib.md5()
                with open(fname, "rb") as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        hash_md5.update(chunk)
                HASH_CACHE[key] = hash_md5.hexdigest()
        except FileNotFoundError:
            key = f'{fname}-None'
            HASH_CACHE[key] = None
    return HASH_CACHE[key]

def get_file_data(fname):
    p = Path(fname)
    if not p.exists():
        return {}

    stats = p.stat()
    file_data = {
        'file_name': fname,
        'file_hash': hash_file(fname, stats),
        'file_size': stats.st_size,
        'modified': stats.st_mtime,
    }
    return file_data

#
# Hash all the lines of a file. Should only be applied to a text file
#
def hash_file_lines(fname, file_type):
    hashes = []
    text = ""

    if file_type == "application/pdf":
        return hash_pdf_file_lines(fname)

    if file_type == "text/csv":
        return hash_csv_file_lines(fname)

    if not is_binary(fname):
        with open(fname, "rt") as f:
            for line in f:
                text = text + " " + line
                line = line.strip().encode()
                hashes.append(hashlib.md5(line).hexdigest())

    return hashes

def getShinglesFname(fname, file_type):
    text = ""
    if file_type != "text/csv":
        with open(fname, "rt") as f:
            for line in f:
                text = text + " " + line
    return getShingles(text, fname, file_type)

def hash_csv_file_lines(fname):
    hashes = []
    with open(fname, "rt") as f:
        i = 0
        next_read = 0
        for line in f:
            if i == next_read:
                hashes.append(hashlib.md5(line.strip().encode()).hexdigest())

                if i < 100:
                    next_read += 1
                elif i < 1000:
                    next_read += 5
                elif i < 10000:
                    next_read += 50
                elif i < 100000:
                    next_read += 500
                else:
                    next_read += 1000

            i += 1

    return hashes

## This is very slow
## near-miss detection (hash-shingling)
def hash_pdf_file_lines(fname):
    hashes = []
    pdfFileObj = open(fname, 'rb')
    pdfReader = PyPDF2.PdfFileReader(pdfFileObj, strict=False)
    try:
        for pageNumber in range(pdfReader.getNumPages()):
            pageObj = pdfReader.getPage(pageNumber)
            # hashes.append(hashlib.md5(pageObj.extractText().strip().encode()).hexdigest())
            lines = pageObj.extractText().splitlines()
            for line in lines:
                line = line.strip().encode()
                hashes.append(hashlib.md5(line).hexdigest())
        pdfFileObj.close()
    except PyPDF2.utils.PdfReadError as e:
        # PyPDF2 has trouble with some PDFs
        print("Problem hashing PDF lines: ", e)

    return hashes

# def send_synclist_thread(user, observationList, comment=None):
def observeAndSyncThread(user, file_loc):
    user.observeAndSync(file_loc)

#
# Transmit observations to the server
#
def send_synclist(user, observationList, file_loc, comment=None):
    knps_version = get_version(file_loc)
    install_id = user.get_install_id()
    hostname = socket.gethostname()
    print("KNPS Version: ", knps_version)

    url = "{}/synclist/{}".format(user.get_server_url(), user.username)

    login = {
        'username': user.username,
        'access_token': user.access_token
    }

    obsList = []
    for file_name, file_hash, file_type, line_hashes, optionalItems in observationList:
        p = Path(file_name)
        info = p.stat()

        metadata = {
            'username': user.username,
            'file_name': file_name,
            'file_hash': file_hash,
            'filetype': file_type,
            'line_hashes': line_hashes,
            'file_size': info.st_size,
            'modified': info.st_mtime,
            'knps_version': knps_version,
            'install_id': install_id,
            'hostname': hostname,
            'optionalItems': optionalItems
        }
        obsList.append({'metadata': metadata})

    fDict = {'observations': json.dumps(obsList)}

    response = requests.post(url, files=fDict, data=login)
    obj_data = response.json()

    return obj_data


#
# Transmit observations to the server
#
def send_process_sync(user, process, file_loc=None, comment=None):
    knps_version = get_version(file_loc)
    install_id = user.get_install_id()
    hostname = socket.gethostname()
    print("KNPS Version: ", knps_version)

    url = "{}/syncprocess/{}".format(user.get_server_url(), user.username)

    login = {
        'username': user.username,
        'access_token': user.access_token
    }

    fDict = {'process': json.dumps(process, default=str)}
    response = requests.post(url, files=fDict, data=login)
    obj_data = response.json()

    return obj_data

#
# Transmit observations to the server
#
def send_adornment(user, filename, comment):
    url = "{}/adorn/{}".format(KNPS_SERVER_URL, user.username)

    login = {
        'username': user.username,
        'access_token': user.access_token
    }

    fDict = {'filename': json.dumps(filename), 'comment': json.dumps(comment)}
    response = requests.post(url, files=fDict, data=login)
    obj_data = response.json()

    return obj_data


#
# Transmit observations to the server
#
def send_createdataset(user, id, title, desc, targetHash):
    url = "{}/createdataset/{}".format(KNPS_SERVER_URL, user.username)

    login = {
        'username': user.username,
        'access_token': user.access_token
    }

    fDict = {'id': json.dumps(id), 'title': json.dumps(title), 'desc': json.dumps(desc), 'targetHash': json.dumps(targetHash)}
    response = requests.post(url, files=fDict, data=login)
    obj_data = response.json()

    return obj_data

def hash_CSV_columns(fname):
    hashes = []
    cols = []
    with open(fname, "rt") as f:
        dialect = csv.Sniffer().sniff(f.read(1024))
        csvfile.seek(0)
        reader = csv.reader(f, dialect)
        for row in reader:
            for i, x in enumerate(row):
                if len(cols) <= i:
                    cols.append([])
                cols[i].append(x)

    for column in cols:
        column_str = ''.join(column).strip().encode()
        hashes.append(hashlib.md5(column_str).hexdigest())
    return hashes

def hash_image(fname):
    #Have this take in and hash an image through thing described earlier.
    return None

def get_csv_file_shingles(fname, fingerprint_bytes):
    hashes = []
    with open(fname, "rt") as f:
        i = 0
        next_read = 0
        for line in f:
            if i == next_read:
                hashes.append(int.from_bytes(hashlib.sha256(line.strip().encode()).digest()[:fingerprint_bytes], 'little'))

                if i < 100:
                    next_read += 1
                elif i < 1000:
                    next_read += 5
                elif i < 10000:
                    next_read += 50
                elif i < 100000:
                    next_read += 500
                else:
                    next_read += 1000

            i += 1

    return hashes

# This fucntion removes punctiation and whitespace.
# The returns a list of tokens(words) and should not contain any lists or anything along those lines.
def createShingleFingerprints(s, fname, file_type, shingle_length = 5, fingerprint_bytes = 8):
    if file_type == "text/csv":
        return get_csv_file_shingles(fname, fingerprint_bytes)
    s = re.sub(r'[^\w\s]', '', s)
    words = s.lower().split()
    shingles = []
    if len(words) <= shingle_length:
        fingerprint = int.from_bytes(hashlib.sha256(words.__str__().encode()).digest()[:fingerprint_bytes], 'little')
        return {fingerprint}
    for i in range(len(words)-shingle_length):
        fingerprint = int.from_bytes(hashlib.sha256(words[i:i+shingle_length].__str__().encode()).digest()[:fingerprint_bytes], 'little')
        shingles.append(fingerprint)
    return shingles

## So for each s
## This function should find shingles.
def getShingles(s, fname, file_type, shingle_length = 5, num_shingles = 10, fingerprint_bytes = 8):
    random.seed(0)
    shingles = []
    all_shingles = createShingleFingerprints(s, fname, file_type, shingle_length, fingerprint_bytes)

    if all_shingles:
        for i in range(num_shingles):
            factor = int(random.random()*(256**fingerprint_bytes))
            shift = int(random.random()*(256**fingerprint_bytes))
            new_shingles = {}
            for shingle in all_shingles:
                new_shingles[(factor*shingle+shift)%(256**fingerprint_bytes)] = shingle
            minimum = min(new_shingles.keys())
            shingles.append(str(new_shingles[minimum]))
    return shingles

## use the mimetype
def get_file_type(f):
    type, encoding = mimetypes.guess_type(f)

    if type:
        return type
    elif is_binary(f):
        return 'binary/unknown'
    else:
        return 'text/unknown'


## check if the file is binary by trying to open it


class Error(Exception):
    """Base class for exceptions in this module."""
    pass

class WrongUserError(Error):
    """Exception raised for errors when the wrong users tries to update KNPS.

    Attributes:
        message -- explanation of the error
    """

    def __init__(self, message):
        self.message = message

#
# Represents, um, a User.
#
class User:
    def __init__(self):
        self.load_db()
        self.username, self.access_token = self.get_current_user()

    def login(self, username=None):
        # FOR TEMP LOGIN.
        if username:
            self.username = username
            self.access_token = "INSECURE_TOKEN_{}".format(username)
            if self.username not in self.db:
                self.db[self.username] = {}

            self.db['__CURRENT_USER__'] = (self.username, self.access_token)
            self.save_db()
            print("You are now logged in as: {}".format(username))

        else:
            ROOTURL = self.get_server_url()
            url = ROOTURL + "/cli_login"

            response = requests.get(url)
            data = response.json()

            if 'login_url' in data:
                print("Opening web browser for login...")
                webbrowser.open(data['login_url'])
                token_url = ROOTURL + "/get_token"
                token_response = requests.post(token_url, data={'login_code': data['login_code']})
                token_data = token_response.json()

                username = token_data['email']
                # This is not really a login. TODO: make this good
                self.username = username
                self.access_token = token_data['access_token']

                if self.username not in self.db:
                    self.db[self.username] = {}

                self.db['__CURRENT_USER__'] = (self.username, self.access_token)
                self.save_db()

                print("You are now logged in as: {}".format(token_data['email']))

    def logout(self):
        ROOTURL = self.get_server_url()
        url = ROOTURL + "/cli_logout"

        response = requests.post(url, data={'access_token': self.access_token})
        data = response.json()

        if 'logout_url' in data:
            del(self.db['__CURRENT_USER__'])
            self.username = None
            self.access_token = None
            self.save_db()

            print("Opening web browser for logout...")
            webbrowser.open('https://dev-66403161.okta.com/login/signout')

    def load_db(self):
        p = Path(Path.home(), DB_FILE)
        if p.exists():
            self.db = json.load(p.open())
        else:
            self.db = {}

    def save_db(self):
        json.dump(self.db, Path(Path.home(), DB_FILE).open('wt'), indent=2)

    def get_current_user(self):
        (u, h) = self.db.get('__CURRENT_USER__', (None, None))
        return u, h

    def set_server(self, url):
        # TODO: do some validation here
        self.db['__SERVER__'] = url
        self.save_db()

    def get_server(self):
        return self.db.get('__SERVER__', 'dev')

    def get_server_url(self):
        url = self.get_server()

        if url == 'prod':
            url = KNPS_SERVER_PROD
        elif url == 'dev':
            url = KNPS_SERVER_DEV

        return 'http://{}'.format(url)

    def set_store(self, shouldStore):
        # TODO: do some validation here
        self.db['__STORE__'] = bool(distutils.util.strtobool(shouldStore))
        self.save_db()

    def get_store(self):
        return self.db.get('__STORE__', False)

    def get_install_id(self):
        if not self.db:
            self.load_db()

        if '__INSTALL_ID__' not in self.db:
            self.db['__INSTALL_ID__'] = str(uuid.uuid1())
            self.save_db()

        return self.db['__INSTALL_ID__']

    def add_dir(self, d):
        if 'dirs' not in self.db[self.username]:
            self.db[self.username]['dirs'] = []

        if d not in self.db[self.username]['dirs']:
            self.db[self.username]['dirs'].append(str(d))
            self.save_db()

    def get_dirs(self):
        return self.db[self.username].get('dirs', [])

    def get_files(self):
        files = []
        for d in self.get_dirs():
            files += [os.path.join(r, file) for r,d,f in os.walk(d) if '.knps' not in r for file in f]

        return files

    def getNextTodoList(self):
        todoDict = self.db[self.username].get("todos", {})
        if len(todoDict) == 0:
            return None
        for k, v in todoDict.items():
            return (k, v)

    def removeTodoList(self, todoKey):
        todoDict = self.db[self.username].get("todos", {})
        todoDict.pop(todoKey, None)
        self.save_db()

    def addTodoList(self, todoList):
        todoDict = self.db[self.username].setdefault("todos", {})
        todoDict[datetime.now().microsecond] = todoList
        self.save_db()

#
# This maintains a user's set of watched files and dirs
#
class Watcher:
    def __init__(self, user):
        self.user = user
        self.config = None
        self.db = None
        self.knps_version = get_version()

    #
    # Add something to the watchlist
    #
    def watch(self, directory):
        p = Path(directory)

        if not p.exists() and p.is_dir():
            raise NotADirectoryError("Cannot watch: not a directory")

        # Make sure it's an absolute path
        dir = p.resolve()
        cfg = self.__get_cfg__(dir)
        # The target shouldn't already have a .knps config dir
        if cfg.exists():
            self.config = configparser.ConfigParser()
            self.config.read(self.__get_cfg__(dir))

            if self.config['KNPS']['user'] == self.user.username:
                raise Error("You're already watching that directory")
            else:
                raise WrongUserError("You can't watch that directory. User '{}' is already watching it (But you're '{}'.)".format(self.config['KNPS']['user'], self.user.username))


        # The target dir doesn't seem to be watched, so go ahead!
        self.__make_cfg__(dir)
        self.user.add_dir(dir)
        print("Watching {}".format(dir))


    def __get_cfg__(self, d):
        return d.joinpath(CFG_DIR, CFG_FILE)

    def __make_cfg__(self, d):
        cfg_dir = d.joinpath(CFG_DIR)
        cfg_dir.mkdir(exist_ok=True)

        self.config = configparser.ConfigParser()
        self.config.read(self.__get_cfg__(d))
        self.config.add_section('KNPS')
        self.config.set('KNPS', 'user', self.user.username)

        self.config.write(self.__get_cfg__(d).open("w"))

    def __load_local_db__(self):
        p = Path(Path.home(), DIR_DB_FILE)
        db_version = None
        if p.exists():
            try:
                self.db = json.load(p.open())
                db_version = self.db.get('__KNPS_VERSION__', None)
            except Exception as e:
                print(e)
                pass

        if not self.db or db_version != self.knps_version:
            self.db = {'__KNPS_VERSION__': self.knps_version}

        if self.user.username not in self.db:
            self.db[self.user.username] = {}

        if self.user.get_server() not in self.db[self.user.username]:
            self.db[self.user.username][self.user.get_server()] = {}

    def __save_local_db__(self):
        json.dump(self.db, Path(Path.home(), DIR_DB_FILE).open('wt'), indent=2)

    def file_already_processed(self, f):
        if not CACHE_FILE_PROCESSING:
            return False

        if not self.db:
            self.__load_local_db__()

        file_hash = hash_file(f)

        return file_hash in self.db[self.user.username][self.user.get_server()] and f in self.db[self.user.username][self.user.get_server()][file_hash]

    def record_file_processing(self, f):
        if CACHE_FILE_PROCESSING:
            if not self.db:
                self.__load_local_db__()

            file_hash = hash_file(f)
            if file_hash not in self.db:
                self.db[self.user.username][self.user.get_server()][file_hash] = {}
            self.db[self.user.username][self.user.get_server()][file_hash][f] = 1

    #
    # Comment on an observed file
    #
    def addComment(self, f, comment):
        # Must clear the observation sync list first
        self.observeAndSync()

        # Transmit observation and comment
        send_adornment(self.user, str(Path(f).resolve()), comment)

    #
    # Create a Dataset for a given file.
    #
    def addDataset(self, configYamlFile):
        #self.observeAndSync()

        with open(configYamlFile, "r") as stream:
            configYaml = yaml.safe_load(stream)

            send_createdataset(self.user, configYaml["id"], configYaml["title"], configYaml["desc"], configYaml["targetHash"])

    #
    # Collect some observations
    #
    def observeAndSync(self, file_loc = None, process=None):
        if file_loc == None:
            file_loc = __file__
        # If there are TODO items outstanding, great.
        todoPair = self.user.getNextTodoList()



        #
        # If not, then we need to generate a TODO list and add it
        # The lists should be up to K items long. That lets us
        # make incremental progress in case the program gets
        # interrupted.
        #
        if todoPair is None or process:
            print("No existing observation list. Formulating new one...")
            k = 50

            file_list = self.user.get_files()

            if process and len(process['output_files']) + len(process['access_files']) > 0:
                process_files = set.union(process['input_files'], process['output_files'], process['access_files'])
                file_list = [value for value in process_files if value in file_list]

            longTodoList = [x for x in file_list]
            smallTodoLists = [longTodoList[i:i+k] for i in range(0, len(longTodoList), k)]

            for smallTodoList in smallTodoLists:
                self.user.addTodoList(smallTodoList)
            todoPair = self.user.getNextTodoList()

        print("Processing observation list...")

        #
        # Now finish all outstanding TODO lists. Mark them
        # as done as we go.
        #
        self.__load_local_db__()
        file_hashes = {}
        while todoPair is not None:
            k, todoList = todoPair

            # Process what's on the TODO list, upload it a chunk at a time
            observationList = []
            todoChunk = todoList
            skipCount = 0
            uploadCount = 0
            for f in todoChunk:
                print("Processing", f)
                try:
                    file_hashes[f] = hash_file(f)

                    #if self.file_already_processed(f):
                    #    print(" -- Already processed")
                    #    continue


                    observationList.append(self._observeFile_(f))
                    uploadCount += 1
                    self.record_file_processing(f)
                except Exception as e:
                    print("*** Skipping: {}".format(e))
                    skipCount += 1

            print("Sending the synclist")
            response = send_synclist(self.user, observationList, file_loc)
            if 'error' in response:
                print('ERROR: {}'.format(response['error']))
                break
            else:
                print("Observed and uploaded", uploadCount, "items. Skipped", skipCount)
                self.__save_local_db__()

                # Mark the TODO list as done
                self.user.removeTodoList(k)

                # Get the next one if available
                todoPair = self.user.getNextTodoList()

        # Now process the process
        if process and len(process['outputs']) + len(process['accesses']) > 0:
            knps_version = get_version(file_loc)
            install_id = self.user.get_install_id()
            hostname = socket.gethostname()

            process['input_files'] = [(x, file_hashes[x]) for x in process['input_files'] if x in file_hashes]
            process['output_files'] = [(x, file_hashes[x]) for x in process['output_files'] if x in file_hashes]
            process['access_files'] = [(x, file_hashes[x]) for x in process['access_files'] if x in file_hashes]
            process['username'] = self.user.username
            process['knps_version'] = knps_version
            process['install_id'] = install_id
            process['hostname'] = hostname
            print(json.dumps(process, indent=2, default=str))

            send_process_sync(self.user, process, file_loc=file_loc)

    #
    # This is where we collect observation data.
    #
    # The input is a file path.
    #
    # The output is a tuple with 4 elements:
    # 1) The username
    # 2) The file path
    # 3) The file hash
    # 4) Line hashes
    # 5) A dictionary of optional objects. This can vary according to
    #    the file type or whatever we like. IF YOU ARE ADDING NEW INFO
    #    DURING THE PROFILE STAGE, ADD IT TO THIS DICTIONARY!
    #
    ### TODO:
    ### more ways to do partial hashes (based upon file type)

    def _observeFile_(self, f):
        file_hash = hash_file(f)
        file_type = get_file_type(f)
        line_hashes = hash_file_lines(f, file_type)
        optionalFields = {}
        optionalFields["filetype"] = file_type

        if self.user.get_store():
            if os.stat(f).st_size < 10 * 1000 * 1000:
                optionalFields["content"] = codecs.encode(open(f, "rb").read(), "base64").decode("utf-8")



        ##CSV_Column_hashs
        # if "csv" in file_type:
        #     column_hashes = hash_CSV_columns(f)
        #     optionalFields["column_hashes"] = column_hashes

        # if file_type.startswith("text/") and file_type != "text/csv":
        if file_type.startswith("text/"):
            shingles = getShinglesFname(f, file_type)
            optionalFields["shingles"] = shingles
        return (f, file_hash, file_type, line_hashes, optionalFields)




class Monitor:
    def __init__(self, user):
        self.user = user
        self.watcher = Watcher(self.user)
        self.config = None
        self.db = None
        self.knps_version = get_version()

        self.dirs = self.user.get_dirs()

        self.proc_cache = {}

    def __get_process__(self, procname, threadid, filename):
        proc_key = f'{procname}.{threadid}'
        if proc_key not in self.proc_cache:
            for proc in psutil.process_iter():
                try:
                    pinfo = proc.as_dict(attrs=['pid', 'name', 'cmdline', 'open_files'])
                    # if pinfo['open_files'] != None:
                    #     print(pinfo)
                    if pinfo['name'] == procname and pinfo['open_files'] != None:
                        for f in pinfo['open_files']:
                            if filename in f.path:
                                self.proc_cache[proc_key] = pinfo
                        # self.proc_cache[proc_key] = pinfo
                except psutil.NoSuchProcess:
                    pass

            if proc_key not in self.proc_cache:
                return None
        else:
            return self.proc_cache[proc_key]


    def run(self):
        cmd = ['sudo', 'nice', 'fs_usage', '-f filesys', '-w', '-e', 'mds', 'fseventsd', 'mdworker_shared']
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        dir_regex = r'{}'.format('|'.join([x.lstrip('/') for x in self.dirs]))

        blacklist = ['stat64', 'filecoordinationd', 'getattrlist', 'fsgetpath', 'getxattr', 'fsctl', 'statfs64']
        blacklist_regex = r'{}'.format('|'.join(blacklist))

        procs = {}

        for line in proc.stdout:
            dt = datetime.now()

            for key, p in procs.items():
                if 'last_update' in p and 'synced' not in p and len(p['outputs']) > 0:

                    diff = dt - p['last_update']
                    if diff.seconds >= PROCESS_SYNC_AGE_SECONDS:
                        print(f'Syncing {p["name"]}')
                        procs[key]['synced'] = True

                        self.watcher.observeAndSync(process=p)



            line = line.rstrip().decode()
            if re.search(blacklist_regex, line):
                continue

            match = re.search(dir_regex, line)
            if match:
                line_breakdown = line.split(maxsplit = 6)
                if any(substring in line_breakdown[-1] for substring in ["Sublime Text", "bird", "quicklookd"]):
                    continue

                # print(line_breakdown)

                data = re.split(r'\s+', line)
                timestamp = data[0]
                action = data[1]
                process_name, thread_id = data[-1].rsplit('.', 1)
                proc_key = f'{process_name}.{thread_id}'
                pathname = [x for x in data if re.search(dir_regex, x)]
                if len(pathname):
                    pathname = pathname[0]
                    path_obj = Path(pathname)

                    if (os.path.isdir(pathname) or
                        path_obj.name.startswith('~$') or
                        path_obj.name.endswith('.swp$')):
                        continue
                else:
                    pathname = None
                    continue

                open_type = None

                if action == 'open' or action == 'access':
                    open_flags = [x for x in data if re.search(r'^\(.+\)$', x)]
                    if len(open_flags):
                        open_flags = open_flags[0]
                        if open_flags[2] == 'W':
                            open_type = 'write'
                        elif open_flags[1] == 'R':
                            open_type = 'read'


                if proc_key not in procs:
                    procs[proc_key] = {'name': process_name,
                                        'key': proc_key,
                                        'timestamp': dt,
                                        'inputs': set([]),
                                        'outputs': set([]),
                                        'accesses': set([]),
                                        'input_files': set([]),
                                        'output_files': set([]),
                                        'access_files': set([]),
                                        'cmdline': [],
                                        'pid': '',
                                        'file_data': {}}


                proc = self.__get_process__(process_name, thread_id, pathname)

                if proc:
                    procs[proc_key]['cmdline'] = proc['cmdline']
                    procs[proc_key]['pid'] = proc['pid']
                    procs[proc_key]['last_update'] = dt

                # see if the 'accessed' files were modified, if so, they're outputs
                modified_flag = False
                try:
                    last_modified = Path(pathname).stat().st_mtime
                    if last_modified >= procs[proc_key]['timestamp'].timestamp():
                        modified_flag = True
                except:
                    pass

                # print(proc_key, action, open_type, pathname not in procs[proc_key]['outputs'])

                file_data = get_file_data(pathname)

                if not file_data:
                    continue
                file_hash = file_data['file_hash']

                procs[proc_key]['file_data'][file_hash] = file_data

                if ('WrData' in action or open_type == 'write' or (modified_flag and not ('RdData' in action or open_type == 'read'))):
                    procs[proc_key]['outputs'].add(file_hash)
                    procs[proc_key]['output_files'].add(file_data['file_name'])
                    procs[proc_key]['last_update'] = dt
                    procs[proc_key].pop('synced', None) # Need to sync again
                elif ('RdData' in action or open_type == 'read' or not modified_flag):
                    procs[proc_key]['inputs'].add(file_hash)
                    procs[proc_key]['input_files'].add(file_data['file_name'])
                    procs[proc_key]['last_update'] = dt
                    procs[proc_key].pop('synced', None) # Need to sync again
                elif pathname not in procs[proc_key]['accesses'] and file_hash not in procs[proc_key]['inputs'] and file_hash not in procs[proc_key]['outputs']:
                    procs[proc_key]['accesses'].add(file_hash)
                    procs[proc_key]['access_files'].add(file_data['file_name'])
                    procs[proc_key]['last_update'] = dt
                    procs[proc_key].pop('synced', None) # Need to sync again


                # print(f'{timestamp} - {action} - {pathname} - {process_name} - Thread: {thread_id}')
                # print(line)
                print(json.dumps(procs, indent=2, default=str))

EVENT_TAGS = {('moved', True): 'DIR_MOVED',
              ('moved', False): 'FILE_MOVED',
              ('deleted', True): 'DIR_DELETED',
              ('deleted', False): 'FILE_DELETED',
              ('created', True): 'DIR_CREATED',
              ('created', False): 'FILE_CREATED',
              ('modified', True): 'DIR_MODIFIED',
              ('modified', False): 'FILE_MODIFIED',
}

class KNPSLoggingEventHandler(FileSystemEventHandler):
    """Logs all the events captured."""

    def __init__(self, metadata):
        self.metadata = metadata

    def on_any_event(self, event):
        src_file_hash = None
        dest_file_hash = None

        addl_data = dict(self.metadata)
        addl_data['src_path'] = event.src_path

        if not event.is_directory:
            src_file_hash = hash_file(event.src_path)

        if src_file_hash:
            addl_data['src_file_hash'] = src_file_hash

        if event.event_type == 'moved':
            addl_data['dest_path'] = event.dest_path
            if not event.is_directory:
                dest_file_hash = hash_file(event.dest_path)
            if dest_file_hash:
                addl_data['dest_file_hash'] = dest_file_hash

        addl_data['timestamp'] = datetime.now().astimezone().isoformat()
        addl_data['action'] = EVENT_TAGS[(event.event_type, event.is_directory)]
        print(json.dumps(addl_data, indent=2))
        print('---')

class FileMonitor:
    def __init__(self, user):
        self.user = user
        self.watcher = Watcher(self.user)
        self.config = None
        self.db = None
        self.knps_version = get_version()

        self.dirs = self.user.get_dirs()

        self.metadata = {'username': self.user.username,
                         'knps_version': self.knps_version,
                         'knps_source': 'FileMonitor'}

        self.observer = Observer()

    def run(self):
        path = '/Users/mike/tmp/mrander_data'
        event_handler = KNPSLoggingEventHandler(self.metadata)
        observer = Observer()
        observer.schedule(event_handler, path, recursive=True)

        observer.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()

class ProcessMonitor:
    def __init__(self, user):
        self.user = user
        self.watcher = Watcher(self.user)
        self.config = None
        self.db = None
        self.knps_version = get_version()

        self.dirs = self.user.get_dirs()

        self.metadata = {'username': self.user.username,
                         'knps_version': self.knps_version,
                         'knps_source': 'ProcessMonitor'}

        self.observer = Observer()

    def __make_process_key__(self, p):
        return f"{p['pid']}_{p['create_time']}"


    def run(self):
        path = '/Users/mike/tmp/mrander_data'

        processes = {}

        try:
            while True:
                # time.sleep(1)

                for proc in psutil.process_iter():
                    try:
                        pinfo = proc.as_dict(attrs=['pid', 'name', 'cmdline', 'open_files', 'create_time'])

                        if pinfo['open_files'] != None:
                            open_files = pinfo['open_files']
                            key = self.__make_process_key__(pinfo)
                            if not processes.get(key, None) == open_files:
                                processes[key] = open_files
                                for f in open_files:
                                    if path in f.path:
                                        pinfo['open_files'] = [x.path for x in open_files]
                                        pinfo['timestamp'] = datetime.now().astimezone().isoformat()
                                        pinfo['action'] = 'PROCESS_OBSERVED'
                                        print(json.dumps(self.metadata | pinfo, indent=2))
                                        print('---')
                                        break

                    except psutil.NoSuchProcess:
                        pass

        except KeyboardInterrupt:
            print("Done")

from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

class CustomHttpHandler(logging.Handler):
    def __init__(self, url: str, token: str, silent: bool = True):
        '''
        Initializes the custom http handler
        Parameters:
            url (str): The URL that the logs will be sent to
            token (str): The Authorization token being used
            silent (bool): If False the http response and logs will be sent
                           to STDOUT for debug
        '''
        self.url = url
        self.token = token
        self.silent = silent

        # sets up a session with the server
        self.MAX_POOLSIZE = 100
        self.session = session = requests.Session()

        session.headers.update({
            'Content-Type': 'application/json',
            'Authorization': 'Bearer %s' % (self.token)
        })

        self.session.mount('http://', HTTPAdapter(
            max_retries=Retry(
                total=5,
                backoff_factor=0.5,
                status_forcelist=[403, 500]
            ),
            pool_connections=self.MAX_POOLSIZE,
            pool_maxsize=self.MAX_POOLSIZE
        ))

        super().__init__()

    def emit(self, record):
        '''
        This function gets called when a log event gets emitted. It recieves a
        record, formats it and sends it to the url
        Parameters:
            record: a log record
        '''
        logEntry = self.format(record)
        response = self.session.post(self.url, data=logEntry)

        if not self.silent:
            print(logEntry)
            print(response.content)

class LogMessage:
    def __init__(self, user):
        self.user = user
        self.knps_version = get_version()

        self.uuid = str(uuid.uuid4())
        self.install_id = user.get_install_id()

        self.data = {'message_id': self.uuid}

        self.data['metadata'] = {'username': self.user.username,
                                 'install_id': self.install_id,
                                 'knps_version': self.knps_version,
                                 'source': 'LogMessageBuilder',
                                 'message_start_time': datetime.now().astimezone().isoformat()}

    def add_metadata(self, key, value):
        if 'metadata' not in self.data:
            self.data['metadata'] = {}
        self.data['metadata'][key] = value

    def add_input(self, path=None, hash=None, timestamp=None, comment=None, extra=None):
        if 'inputs' not in self.data:
            self.data['inputs'] = []

        input = {}
        if path:
            input['path'] = path
        if hash:
            input['hash'] = hash
        if timestamp:
            input['timestamp'] = timestamp
        if comment:
            input['comment'] = comment
        if extra:
            input['extra'] = extra

        self.data['inputs'].append(input)

    def add_output(self, path=None, hash=None, timestamp=None, comment=None, extra=None):
        if 'outputs' not in self.data:
            self.data['outputs'] = []

        output = {}
        if path:
            output['path'] = path
        if hash:
            output['hash'] = hash
        if timestamp:
            output['timestamp'] = timestamp
        if comment:
            output['comment'] = comment
        if extra:
            output['extra'] = extra

        self.data['outputs'].append(output)

    def add_action(self, type=None, name=None, timestamp=None, comment=None, extra=None):
        action = {}
        if type:
            action['type'] = type
        if name:
            action['name'] = name
        if timestamp:
            action['timestamp'] = timestamp
        if comment:
            action['comment'] = comment
        if extra:
            action['extra'] = extra

        self.data['action'] = action

    def add_comment(self, comment):
        self.data['comment'] = comment

    def add_extra(self, extra):
        # This should be a dict
        self.data['extra'] = extra

class Logger:
    def __init__(self, user, token, use_dev=False):
        self.user = user
        self.token = token

        self.watcher = Watcher(self.user)
        self.config = None
        self.db = None
        self.knps_version = get_version()

        self.dirs = self.user.get_dirs()

        # create logger
        self.logger = logging.getLogger('KNPS_LOGGER')
        self.logger.setLevel(logging.INFO)
        # create formatter - this formats the log messages accordingly
        formatter = logging.Formatter('%(message)s')

        if use_dev:
            self.log_server = KNPS_SERVER_DEV
        else:
            self.log_server = KNPS_SERVER_PROD

        # create a custom http logger handler
        httpHandler = CustomHttpHandler(
            url=f'http://{self.log_server}/log',
            token=self.token,
            silent=False
        )

        httpHandler.setLevel(logging.INFO)

        # add formatter to custom http handler
        httpHandler.setFormatter(formatter)

        # add handler to logger
        self.logger.addHandler(httpHandler)

    def get_token(self):
        if self.user:
            response = requests.post(f'http://{self.log_server}/get_log_token', data={'username': self.user.username})
            obj_data = response.json()
            return obj_data.get('token', 'No token')
        return 'No token available'

    def log(self, message):
        try:
            if type(message) == LogMessage:
                message.data['metadata']['send_time'] = datetime.now().astimezone().isoformat()
                json_msg = json.dumps(message.data)
            else:
                json_msg = json.dumps({
                    'send_time': datetime.now().astimezone().isoformat(),
                    'username': self.user.username,
                    'knps_version': self.knps_version,
                    'knps_source': 'LoggerAPI'} | message
                )
            self.logger.info(json_msg)
        except TypeError:
            raise TypeError('KNPS Logger payload must be a dict or LogMessage object.')

    def start_message(self):
        return LogMessage(self.user)

#
# main()
#
def main():
    # execute only if run as a script
    parser = argparse.ArgumentParser(description='KNPS command line')

    parser.add_argument("--login", action="store_true", help="Perform login")
    parser.add_argument("--login_temp", help="Temporary dev login bypass. INSECURE!")
    parser.add_argument("--logout", action="store_true", help="Logout current user")
    parser.add_argument("--status", nargs="*", help="Check KNPS status", default=None)
    parser.add_argument("--watch", help="Add a directory to watch")
    parser.add_argument("--comment", nargs="+", help="Add a comment to a data object")
    parser.add_argument("--addDataset", help="Add a Dataset to the graph. Takes a YAML file")
    parser.add_argument("--sync", action="store_true", help="Sync observations to service")
    parser.add_argument("--server", help="Set KNPS server. Options: dev, prod, or address:port")
    parser.add_argument("--monitor", action="store_true", help="Run KNPS as a process and file system monitor.")
    parser.add_argument("--proc_logger", action="store_true", help="Run KNPS as a process and file system monitor.")
    parser.add_argument("--file_logger", action="store_true", help="Run KNPS as a process and file system monitor.")
    parser.add_argument("--log", action="store_true", help="Run KNPS as a process and file system monitor.")
    parser.add_argument("--store", help="Upload bytes in addition to metadata. Options: True or False (default)")
    parser.add_argument("--version", action="store_true", help="Display version information")
    parser.add_argument("--get_token", action="store_true", help="Get token for logging API")
    parser.add_argument("--get_dev_token", action="store_true", help="Get token for logging API (dev server)")
    parser.add_argument('args', type=str, help="KNPS command arguments", nargs='*' )

    args = parser.parse_args()

    u = User()
    if args.login:
        u.login()
    elif args.login_temp:
        # TODO: remove this. this is a temporary login bypass for dev purposes
        u.login(args.login_temp)
    elif args.logout:
        u.logout()
    elif args.watch:
        if not u.username:
            print("Not logged in.")
        else:
            w = Watcher(u)
            w.watch(args.watch)

    elif args.comment:
        if not u.username:
            print("Not logged in.")
        else:
            if len(args.comment) < 2:
                print("Provide the target filename and at least a 1-word comment")
                sys.exit(0)

            w = Watcher(u)
            w.addComment(args.comment[0], " ".join(args.comment[1:]))

    elif args.addDataset:
        if not u.username:
            print("Not logged in")
        else:
            w = Watcher(u)
            w.addDataset(args.addDataset)

    elif args.status is not None:
        print("KNPS Version: ", get_version())
        if not u.username:
            print("Not logged in; please run: knps --login")
        else:
            print("User: {}    Server: {}".format(u.username, u.get_server()))
            print("Upload bytes? {}".format(u.get_store()))
            print()
            dirs = u.get_dirs()
            files = u.get_files()
            print("You have {} top-level directories and {} files watched by knps.".format(len(dirs), len(files)))

            if 'dirs' in args.status:
                print("\nWatched directories:")
                for d in dirs:
                    print("   {}".format(d))

            if 'files' in args.status:
                print("\nWatched files:")
                for d in files:
                    print("   {}".format(d))

        if 'dirs' in args.status or 'files' in args.status:
            print()

    elif args.sync:
        print("KNPS Version: ", get_version())
        if not u.username:
            print("Not logged in.")
        else:
            Watcher(u).observeAndSync()
            # thread = threading.Thread(target = observeAndSyncThread, args = (Watcher(u), __file__))
            # thread.start()


    elif args.server:
        print("Setting KNPS server to: {}".format(args.server))
        u.set_server(args.server)

    elif args.store:
        print("Setting KNPS byte storage flag to: {}".format(args.store))
        u.set_store(args.store)

    elif args.version:
        print(f'KNPS Version: {get_version()}')

    elif args.monitor:
        if not u.username:
            print("Not logged in; please run: knps --login")
        else:
            m = Monitor(u)
            m.run()
    elif args.get_token:
        if not u.username:
            print("Not logged in; please run: knps --login")
        else:
            logger = Logger(u, None)
            token = logger.get_token()
            if token:
                print(f'API token: {token}')
    elif args.get_dev_token:
        if not u.username:
            print("Not logged in; please run: knps --login")
        else:
            logger = Logger(u, None, True)
            token = logger.get_token()
            if token:
                print(f'API token: {token}')
    elif args.proc_logger:
        if not u.username:
            print("Not logged in; please run: knps --login")
        else:
            m = ProcessMonitor(u)
            m.run()
    elif args.file_logger:
        if not u.username:
            print("Not logged in; please run: knps --login")
        else:
            m = FileMonitor(u)
            m.run()
    elif args.log:
        logger = Logger(u.username, '9d06f85af8305616804aec86f9ec848a')
        logger.log({'aaaaa': 1})

if __name__ == "__main__":
    main()

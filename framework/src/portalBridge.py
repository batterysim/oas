# -------------------------------------------------------------------------------
# Copyright 2006-2012 UT-Battelle, LLC. See LICENSE for more information.
# -------------------------------------------------------------------------------
import re

import datetime
from component import Component
import sys, urllib, os
from subprocess import Popen, PIPE
import urllib2
import time
import ipsutil
import inspect
from collections import defaultdict
import hashlib
import glob
import itertools
from convert_log_function import *
import json

ENABLE_MPO = False

def configure_mpo():
    global mpo_cert, mpo_api, mpo
    # Use this if you want to include modules from a subfolder or relative path.
    cmd_subfolder = os.path.realpath(os.path.abspath(os.path.join(os.path.split(
        inspect.getfile(inspect.currentframe()))[0], "/home/elwasif/Projects/atom/MPO/client/python")))
    if cmd_subfolder not in sys.path:
        sys.path.insert(0, cmd_subfolder)
    try:
        from mpo_arg import mpo_methods as mpo
    except ImportError:
        pass
    mpo_cert = '/home/elwasif/Projects/atom/MPO/MPO Demo User.pem'
    mpo_api = 'https://mpo.psfc.mit.edu/test-api'


def hash_file(file_name):
    '''
    Return the MD5 hash of a file
    :rtype: str
    :param file_name: Full path to file
    :return: MD5 of file_name
    '''
    BLOCKSIZE = 65536
    hasher = hashlib.md5()
    with open(file_name, 'rb') as afile:
        buf = afile.read(BLOCKSIZE)
        while len(buf) > 0:
            hasher.update(buf)
            buf = afile.read(BLOCKSIZE)
    return hasher.hexdigest()


class PortalBridge(Component):
    """
    Framework component to communicate with the `SWIM web portal <http://swim.gat.com:8080/monitor/>`_.
    """

    class SimulationData(object):
        """
        Container for simulation data.
        """

        def __init__(self):
            self.counter = 0
            self.monitor_file_name = ""
            self.portal_runid = None
            self.sim_name = ''
            self.sim_root = ''
            self.monitor_file = None
            self.json_monitor_file = None
            self.phys_time_stamp = -1
            self.monitor_url = None
            self.mpo_steps = [None]
            self.mpo_wid = None
            self.bigbuf = ""

    def __init__(self, services, config):
        """
        Declaration of private variables and initialization of
        :py:class:`component.Component` object.
        """
        Component.__init__(self, services, config)
        self.host = ''
        self.curTime = time.localtime()
        self.startTime = self.curTime
        self.services = services
        self.sim_map = {}
        self.runid_url = None
        self.portal_url = None
        self.done = False
        self.first_event = True
        self.childProcess = None
        self.mpo = None
        self.mpo_name_counter = defaultdict(lambda: 0)
        self.file_hash_cache = defaultdict(dict)
        self.file_uid_cache = defaultdict(dict)
        self.counter = 0
        self.dump_freq = 10
        self.write_to_htmldir = True


    def init(self, timestamp=0.0):
        """
        Try to connect to the portal, subscribe to *_IPS_MONITOR* events and
        register callback :py:meth:`.process_event`.
        """
        # try:
        #     self.portal_url = self.PORTAL_URL
        # except AttributeError:
        #     pass
        # try:
        #     self.runid_url = self.RUNID_URL
        # except AttributeError:
        #     pass
        self.host = self.services.get_config_param('HOST')
        self.services.subscribe('_IPS_MONITOR', "process_event")
        try:
            freq = int(self.services.get_config_param("HTML_DUMP_FREQ", silent = True))
        except Exception as e:
            pass
        else:
            self.dump_freq = freq

        try:
            ENABLE_MPO = os.environ['ENABLE_MPO']
        except KeyError:
            ENABLE_MPO = False
        else:
            ENABLE_MPO = True
        if ENABLE_MPO:
            configure_mpo()
            try:
                self.mpo = mpo(api_url=mpo_api, cert=mpo_cert, debug=True)
                self.mpo.debug = False
                self.mpo.filter = 'json'
            except Exception as e:
                print "#################", e
                pass
        return

    def step(self, timestamp=0.0):
        """
        Poll for events.
        """
        while not self.done:
            self.services.process_events()
            time.sleep(0.5)

    def finalize(self, timestamp=0.0):
        for sim_name in self.sim_map.keys():
            sim_data = self.sim_map[sim_name]
            try:
                sim_data.monitor_file.close()
                sim_data.json_monitor_file.close()
            except Exception as e:
                pass


    def get_elapsed_time(self):
        """
        Return total elapsed time since simulation started in seconds
        (including a possible fraction)
        """
        self.curTime = time.localtime()
        delta_t = time.mktime(self.curTime) - time.mktime(self.startTime)
        return delta_t

    def process_event(self, topicName, theEvent):
        """
        Process a single event *theEvent* on topic *topicName*.
        """
        event_body = theEvent.getBody()
        #        self.services.debug('Processing : %s -- %s ', topicName, str(event_body))
        sim_name = event_body['sim_name']
        portal_data = event_body['portal_data']
        try:
            portal_data["sim_name"] = event_body['real_sim_name']
        except KeyError:
            portal_data["sim_name"] = sim_name

        if (portal_data['eventtype'] == 'IPS_START'):
            sim_root = event_body['sim_root']
            self.init_simulation(sim_name, sim_root)

        sim_data = self.sim_map[sim_name]
        if (portal_data['eventtype'] == 'PORTALBRIDGE_UPDATE_TIMESTAMP'):
            sim_data.phys_time_stamp = portal_data['phystimestamp']
            return
        else:
            portal_data['phystimestamp'] = sim_data.phys_time_stamp

        if (portal_data['eventtype'] == 'IPS_SET_MONITOR_URL'):
            sim_data.monitor_url = portal_data['vizurl']
        elif sim_data.monitor_url:
            portal_data['vizurl'] = sim_data.monitor_url

        #        portal_data['walltime'] = int(self.get_elapsed_time())
        portal_data['portal_runid'] = sim_data.portal_runid
        portal_data['seqnum'] = sim_data.counter

        self.send_event(sim_data, portal_data)
        sim_data.counter += 1
        self.counter +=1

        if (portal_data['eventtype'] == 'IPS_END'):
            del self.sim_map[sim_name]

        if (len(self.sim_map) == 0):
            if self.childProcess:
                self.childProcess.stdin.close()
                self.childProcess.wait()
            self.done = True
            self.services.debug('No more simulation to monitor - exiting')
            time.sleep(1)
        return

    def send_event(self, sim_data, event_data):
        """
        Send contents of *event_data* and *sim_data* to portal.
        """
        timestamp = ipsutil.getTimeString()
        buf = '%8d %s ' % (sim_data.counter, timestamp)
        for (k, v) in event_data.iteritems():
            if (len(str(v).strip()) == 0):
                continue
            if (' ' in str(v)):
                buf += '%s=\'%s\' ' % (k, str(v))
            else:
                buf += '%s=%s ' % (k, str(v))
        buf += '\n'
        sim_data.monitor_file.write(buf)
        sim_data.bigbuf += buf

        # json.dump(event_data, sim_data.json_monitor_file, indent=3);
        buf = json.dumps(event_data);
        sim_data.json_monitor_file.write("%s\n" % buf)

        freq = self.dump_freq
        if ((self.counter % freq == 0) or (event_data['eventtype'] == 'IPS_END')):
            html_filename = sim_data.monitor_file_name.replace('eventlog', 'html')
            html_page = convert_logdata_to_html(sim_data.bigbuf)
            open(html_filename,"w").writelines(html_page)
            if self.write_to_htmldir:
                try:
                    html_dir = self.services.get_config_param("USER_W3_DIR", silent=True)
                except Exception:
                    self.services.warning("Missing USER_W3_DIR configuration - disabling web-visible logging")
                    self.write_to_htmldir = False
                    pass
                #print "Missing USER_W3_DIR configuration"
                else:
                    if html_dir.strip() == '':
                        self.write_to_htmldir = False
                    else:
                        html_file = os.path.join(html_dir, os.path.basename(html_filename))
                        try:
                            open(html_file, "w").writelines(html_page)
                        except Exception:
                            self.services.exception("Error writing html file into USER_W3_DIR directory")
                            self.write_to_htmldir = False
        #        self.services.debug('Wrote to monitor file : %s', buf)
        if (self.portal_url):
            webmsg = urllib.urlencode(event_data)
            try:
                if (self.first_event):  # First time, launch sendPost.py daemon
                    cmd = os.path.join(sys.path[0], 'sendPost.py')
                    self.childProcess = Popen(cmd, shell=True, bufsize=128,
                                              stdin=PIPE, stdout=PIPE,
                                              stderr=PIPE, close_fds=True)
                    # self.childProcess = popen2.Popen3(cmd, bufsize=128)
                    self.first_event = False
                self.childProcess.stdin.write('%s %s\n' % \
                                              (self.portal_url, webmsg))
                #                self.services.debug('Wrote event to sendPost.py buffer : %s',
                #                                    str(event_data))
                self.childProcess.stdin.flush()
            except Exception, e:
                self.services.exception('Error transmitting event number %6d to %s : %s', \
                                        sim_data.counter, self.portal_url, str(e))
        if (sim_data.mpo_wid):
            self.send_mpo_data(event_data, sim_data)

        return

    def send_mpo_data(self, event_data, sim_data):
        def md5(fname):
            import hashlib
            "Courtesy of stackoverflow 3431825"
            hash_md5 = hashlib.md5()
            with open(fname, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()

        def mpo_add_file(workflow, parent, file, shortname='Need a name', longdesc='did not add a description.'):
            """Add a local file to the workflow attaching to parent. Calculate
            checksum and if the file is already in the mpo database, use the
            already the UID of the already existing file when adding the data
            object - this creates a linkage to the original. The checksum and
            local file path and name are added as metadata.

            This function relies on the user space metadata, ips_checksum
            and ips_filename. The checksum is the md5 sum and the filename
            is expected should have at least a relative qualifying path.

            workflow : workflow_id
            parent : parent_id
            """
            # if file exist, look for its checksum in the database
            try:
                checksum = md5(file)
            except:
                print("checksum could not find file:", file)
                raise

            is_checksum = self.mpo.search('metadata', params={'key': 'ips_checksum', 'value': checksum})
            # search always returns a list of dictionaries
            # if checksum exists, use first dataobject that has it
            # api search results are sorted by time
            # Note, check this with eqdsk dataobject in test-api
            print '%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%'
            #print (is_checksum)
            print len(is_checksum), file, parent
            print '%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%'

            if len(is_checksum) > 0:
                #print (len(is_checksum))
                # uid is chosen to be first occurrence
                # parent_uid is uid of object metadata is attached to.
                file_uid = is_checksum[0]['parent_uid']

                # Create dataobject reference by uid in the workflow
                dataobject = self.mpo.add(workflow, parent, uid=file_uid, name=shortname, desc=longdesc)
                print 'Existing dataobject : id = ', file_uid
                self.mpo.meta(dataobject['uid'], 'ips_checksum', checksum)
                # add filename metadata the dataobject reference
                filepathname_mpo = self.mpo.meta(dataobject['uid'], 'ips_filename', file)
            else:
                print('file', file)
                file_uri = file
                # Create new dataobject by uri and insert reference in to workflow
                dataobject = self.mpo.add(workflow, parent, uri=file_uri, name=shortname, desc=longdesc)
                # add checksum metadata to original data object
                # add function currently only returns uri field, so fetch full record
                full_dataobject = self.mpo.search('dataobject/' + dataobject['uid'])[0]
                print 'New data object', full_dataobject['do_uid']
                # add checksum so dataobject and also
                checksum_mpo = self.mpo.meta(full_dataobject['do_uid'], 'ips_checksum', checksum)
                self.mpo.meta(dataobject['uid'], 'ips_filename', file)
                self.mpo.meta(dataobject['uid'], 'ips_checksum', checksum)
                dataobject = full_dataobject
            return dataobject

        def get_file_uid(path, file_name):
            '''
            Get file_name's cached MPO uid if seen before in simulation
            :param file_name: Full path to file
            :return: tuple (MPO uid if found, None if not found, file_hash)
            '''
            cached_hash_dict = None
            f_uid = None
            full_path = os.path.join(path, file_name)
            hash = hash_file(full_path)
            cached_hash_dict = self.file_hash_cache[file_name]
            try:
                f_uid = cached_hash_dict[hash]
            except KeyError:
                pass
            return (None, hash)    #(f_uid, hash)

        recordable_events = ['IPS_CALL_BEGIN', 'IPS_STAGE_INPUTS', 'IPS_STAGE_OUTPUTS', 'IPS_CALL_END']
        recordable_mpo_activities = ['IPS_CALL_BEGIN']
        comment = event_data['comment']
        event_type = event_data['eventtype']

        if event_type not in recordable_events:
            return
        inp_objs = []
        if event_type == 'IPS_CALL_END':
            del sim_data.mpo_steps[-1]
            return
        try:
            if (event_type == 'IPS_STAGE_INPUTS'):
                r = re.compile(r"^Elapsed time = ([0-9]*\.[0-9]*) Path = ([^ ]*) Files = (.*)")
                o = r.match(comment)
                (t, path, files) = o.groups()
                glist = [glob.glob(os.path.join(path, f)) for f in files.split()]
                for file_name in [os.path.basename(f) for f in itertools.chain(*glist)]:
                    """
                    (f_uid, f_hash) = get_file_uid(path, file_name)
                    f_uid = False
                    if f_uid:
                        mpo_data_obj = self.mpo.add(workflow_ID=sim_data.mpo_wid,
                                                    parentobj_ID=sim_data.mpo_wid['uid'],
                                                    name=file_name,
                                                    desc="An input file",
                                                    uri='file:' + os.path.join(path, file_name),
                                                    uid=f_uid)
                    else:
                        mpo_data_obj = self.mpo.add(workflow_ID=sim_data.mpo_wid,
                                                    parentobj_ID=sim_data.mpo_wid['uid'],
                                                    name=file_name,
                                                    desc="An input file",
                                                    uri='file:' + os.path.join(path, file_name))
                    """
                    mpo_data_obj = mpo_add_file(sim_data.mpo_wid,
                                                sim_data.mpo_wid['uid'],
                                                os.path.join(path, file_name),
                                                shortname = file_name,
                                                longdesc = "An input file")
                    inp_objs.append(mpo_data_obj['uid'])
                    #print "### File = %s  mpo_data_obj['uid'] = %s" % \
                    #      (file_name, mpo_data_obj['uid'])
                    #if not f_uid:
                    #    self.file_hash_cache[file_name][f_hash] = mpo_data_obj['uid']

            if event_type == 'IPS_STAGE_INPUTS' and not inp_objs:
                return

            count = self.mpo_name_counter[sim_data.sim_name + event_data['code']]
            if event_type == 'IPS_CALL_BEGIN':
                target =  event_data['comment'].split()[-1]
                step_name = "%s %d" % (target, count)
            else:
                step_name = "{0:s} {1:s} {2:d}" \
                    .format(event_data['code'].split('_')[-1], event_type, count)

            if (event_type == 'IPS_STAGE_OUTPUTS'):
                r = re.compile(r"^Elapsed time = ([0-9]*\.[0-9]*) Path = ([^ ]*) Files = (.*)")
                o = r.match(comment)
                (t, path, files) = o.groups()
                if not files:
                    return
            activity = self.mpo.step(workflow_ID=sim_data.mpo_wid,
                                     parentobj_ID=sim_data.mpo_steps[-1],
                                     input_objs=inp_objs,
                                     name=step_name,
                                     desc="%s" % event_data['comment'])
            self.mpo_name_counter[sim_data.sim_name + event_data['code']] += 1
            if (event_type == 'IPS_STAGE_OUTPUTS'):
                glist = [glob.glob(os.path.join(path, f)) for f in files.split()]
                for file_name in [os.path.basename(f) for f in itertools.chain(*glist)]:
                    """
                    (f_uid, f_hash) = get_file_uid(path, file_name)
                    if f_uid:
                        mpo_data_obj = self.mpo.add(workflow_ID=sim_data.mpo_wid,
                                                parentobj_ID=activity['uid'],
                                                name=file_name,
                                                desc="An output file",
                                                uri='file:' + os.path.join(path, file_name),
                                                uid=f_uid,
                                                source = f_uid)
                    else:
                        mpo_data_obj = self.mpo.add(workflow_ID=sim_data.mpo_wid,
                                                parentobj_ID=activity['uid'],
                                                name=file_name,
                                                desc="An output file",
                                                uri='file:' + os.path.join(path, file_name))
                    """
                    mpo_data_obj = mpo_add_file(sim_data.mpo_wid,
                                                activity['uid'],
                                                #sim_data.mpo_wid['uid'],
                                                os.path.join(path, file_name),
                                                shortname = file_name,
                                                longdesc = "An output file")
                    #print self.file_hash
                    #print "### File = %s  f_uid = %s  mpo_data_obj['uid'] = %s" % \
                    #      (file_name, f_uid, mpo_data_obj['uid'])
                    #if not f_uid:
                    #    self.file_hash_cache[file_name][f_hash] = mpo_data_obj['uid']

        except Exception as e:
            print "*************", e
        else:
            if event_type in recordable_mpo_activities:
                sim_data.mpo_steps.append(activity['uid'])

    def init_simulation(self, sim_name, sim_root):
        """
        Create and send information about simulation *sim_name* living in
        *sim_root* so the portal can set up corresponding structures to manage
        data from the sim.
        """
        self.services.debug('Initializing simulation : %s -- %s ', sim_name, sim_root)
        sim_data = self.SimulationData()
        sim_data.sim_name = sim_name
        sim_data.sim_root = sim_root

        #sim_data.portal_runid = str(uuid.uuid4())
        d = datetime.datetime.now()
        date_str = "%s.%03d" % (d.strftime("%Y-%m-%dT%H:%M:%S"), int(d.microsecond/1000))
        sim_data.portal_runid = "_".join([self.host, self.USER, date_str])
        if (self.runid_url != None):
            self.services.debug('PORTAL_RUNID_URL = %s', str(self.runid_url))
            try:
                #               raise urllib2.URLError('TEXT')
                f = urllib2.urlopen(self.runid_url, None, 10)
                sim_data.portal_runid = f.read().strip()
            except (urllib2.URLError), e:
                self.services.error('Error obtaining runID from service at %s : %s' % \
                                    (self.runid_url, str(e)))
                self.services.error('Using a datetime instead')
        try:
            self.services.set_config_param('PORTAL_RUNID', sim_data.portal_runid,
                                           target_sim_name=sim_name)
        except Exception:
            self.services.error('Simulation %s is not accessible', sim_name)
            return

        if sim_data.sim_root.strip() == '.':
            sim_data.sim_root=os.environ['IPS_INITIAL_CWD']
        sim_log_dir = os.path.join(sim_data.sim_root, 'simulation_log')
        try:
            os.makedirs(sim_log_dir)
        except OSError, (errno, strerror):
            if (errno != 17):
                self.services.exception('Error creating Simulation Log directory %s : %d %s' % \
                                        (sim_log_dir, errno, strerror))
                raise

        sim_data.monitor_file_name = os.path.join(sim_log_dir,
                                        sim_data.sim_name + '_' + sim_data.portal_runid + '.eventlog')
        try:
            sim_data.monitor_file = open(sim_data.monitor_file_name, 'w', 0)
        except IOError, (errno, strerror):
            self.services.error("Error opening file %s: error(%s): %s" % \
                                (sim_data.monitor_file_name, errno, strerror))
            self.services.error('Using /dev/null instead')
            sim_data.monitor_file = open('/dev/null', 'w')
        json_fname = sim_data.monitor_file_name.replace('eventlog', 'json')
        sim_data.json_monitor_file = open(json_fname, 'w')

        if self.mpo:
            try:
                sim_data.mpo_wid = self.mpo.init(name="SWIM Workflow " + os.environ["USER"],
                                                 desc=sim_data.sim_name,
                                                 wtype="SWIM")
                print "sim_data.mpo_wid = ", sim_data.mpo_wid
            except Exception as e:
                print e
                print "sim_data.mpo_wid = ", sim_data.mpo_wid
                sim_data.mpo_wid = None
                pass
            else:
                sim_data.mpo_steps = [sim_data.mpo_wid['uid']]

        self.sim_map[sim_data.sim_name] = sim_data
        return

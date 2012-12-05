#!/usr/bin/python -tt


import sys
import os
import glob
import time
import multiprocessing
from backend.dispatcher import Worker
from backend import errors
from bunch import Bunch
import ConfigParser
import optparse

def _get_conf(cp, section, option, default):
    """to make returning items from config parser less irritating"""
    if cp.has_section(section) and cp.has_option(section,option):
        return cp.get(section, option)
    return default
        

class CoprBackend(object):
    def __init__(self, config_file=None, ext_opts=None):
        # read in config file
        # put all the config items into a single self.opts bunch
        
        if not config_file:
            raise errors.CoprBackendError, "Must specify config_file"
        
        self.config_file = config_file
        self.ext_opts = ext_opts # to stow our cli options for read_conf()
        self.opts = self.read_conf()

        logdir = os.path.dirname(self.opts.logfile)
        if not os.path.exists(logdir):
            os.makedirs(logdir, mode=0750)

        # setup a log file to write to
        self.logfile = self.opts.logfile
        self.log("Starting up new copr-be instance")

        
        if not os.path.exists(self.opts.worker_logdir):
            os.makedirs(self.opts.worker_logdir, mode=0750)
            
        self.jobs = multiprocessing.Queue()
        self.workers = []
        self.added_jobs = []

        
    def read_conf(self):
        "read in config file - return Bunch of config data"
        opts = Bunch()
        cp = ConfigParser.ConfigParser()
        try:
            cp.read(self.config_file)
            opts.results_baseurl = _get_conf(cp,'backend', 'results_baseurl', 'http://copr')
            opts.frontend_url = _get_conf(cp, 'backend', 'frontend_url', 'http://coprs/rest/api')
            opts.frontend_auth = _get_conf(cp,'backend', 'frontend_auth', 'PASSWORDHERE')
            opts.spawn_playbook = _get_conf(cp,'backend','spawn_playbook', '/etc/copr/builder_playbook.yml')
            opts.terminate_playbook = _get_conf(cp,'backend','terminate_playbook', '/etc/copr/terminate_playbook.yml')
            opts.jobsdir = _get_conf(cp, 'backend', 'jobsdir', None)
            opts.destdir = _get_conf(cp, 'backend', 'destdir', None)
            opts.daemonize = _get_conf(cp, 'backend', 'daemonize', True)
            opts.exit_on_worker = _get_conf(cp, 'backend', 'exit_on_worker', False)
            opts.sleeptime = int(_get_conf(cp, 'backend', 'sleeptime', 10))
            opts.num_workers = int(_get_conf(cp, 'backend', 'num_workers', 8))
            opts.timeout = int(_get_conf(cp, 'builder', 'timeout', 1800))
            opts.logfile = _get_conf(cp, 'backend', 'logfile', '/var/log/copr/backend.log')
            opts.worker_logdir = _get_conf(cp, 'backend', 'worker_logdir', '/var/log/copr/worker/')
            # thoughts for later
            # ssh key for connecting to builders?
            # cloud key stuff?
            # 
        except ConfigParser.Error, e:
            raise errors.CoprBackendError, 'Error parsing config file: %s: %s' % (self.config_file, e)
        
        
        if not opts.jobsdir or not opts.destdir:
            raise errors.CoprBackendError, "Incomplete Config - must specify jobsdir and destdir in configuration"
            
        if self.ext_opts:
            for v in self.ext_opts:
                setattr(opts, v, self.ext_opts.get(v))
        return opts
        
        
    def log(self, msg):
        now =  time.strftime('%F %T')
        output = str(now) + ':' + msg
        if not self.opts.daemonize:
            print output
            
        try:
            open(self.logfile, 'a').write(output + '\n')
        except (IOError, OSError), e:
            print >>sys.stderr, 'Could not write to logfile %s - %s' % (self.logfile, str(e))


    def run(self):

        abort = False
        while not abort:
            for f in sorted(glob.glob(self.opts.jobsdir + '/*.json')):
                n = os.path.basename(f).replace('.json', '')
                if n not in self.added_jobs:
                    self.jobs.put(f)
                    self.added_jobs.append(n)
                    self.log('adding %s' % n)
            
            if self.jobs.qsize():
                self.log("# jobs in queue: %s" % self.jobs.qsize())
                # re-read config into opts
                self.opts = self.read_conf()
                # this handles starting/growing the number of workers
                if len(self.workers) < self.opts.num_workers:
                    self.log("Spinning up more workers for jobs")
                    for i in range(self.opts.num_workers - len(self.workers)):
                        worker_num = len(self.workers) + 1
                        w = Worker(self.opts, self.jobs, worker_num)
                        self.workers.append(w)
                        w.start()
                    self.log("Finished starting worker processes")
                # FIXME - prune out workers
                #if len(self.workers) > self.opts.num_workers:
                #    killnum = len(self.workers) - self.opts.num_workers
                #    for w in self.workers[:killnum]:
                #       #insert a poison pill? Kill after something? I dunno.
                # FIXME - if a worker bombs out - we need to check them
                # and startup a new one if it happens
            # check for dead workers and abort
            for w in self.workers:
                if not w.is_alive():
                    self.log('Worker %d died unexpectedly' % w.worker_num)
                    if self.opts.exit_on_worker:
                        raise errors.CoprBackendError, "Worker died unexpectedly, exiting"
                    

            time.sleep(self.opts.sleeptime)

# lifted from certmaster
def daemonize(pidfile=None): 
    """
    Daemonize this process with the UNIX double-fork trick.
    Writes the new PID to the provided file name if not None.
    """

    pid = os.fork()
    if pid > 0:
        sys.exit(0)
    os.chdir("/")
    os.setsid()
    os.umask(077)
    pid = os.fork()

    os.close(0)
    os.close(1)
    os.close(2)

    # The standard I/O file descriptors are redirected to /dev/null by default.
    if (hasattr(os, "devnull")):  
        REDIRECT_TO = os.devnull
    else:
        REDIRECT_TO = "/dev/null"

    # based on http://code.activestate.com/recipes/278731/
    os.open(REDIRECT_TO, os.O_RDWR)     # standard input (0)

    os.dup2(0, 1)                       # standard output (1)
    os.dup2(0, 2)                       # standard error (2)



    if pid > 0:
        if pidfile is not None:
            open(pidfile, "w").write(str(pid))
        sys.exit(0)

def parse_args(args):
    parser = optparse.OptionParser('\ncopr-be [options]')
    parser.add_option('-c', '--config', default='/etc/copr-be.conf', dest='config_file',
            help="config file to use for copr-be run")
    parser.add_option('-d','--daemonize', default=False, dest='daemonize',
            action='store_true', help="daemonize or not")
    parser.add_option('-p', '--pidfile', default='/var/run/copr-be.pid', dest='pidfile',
            help="pid file to use for copr-be if daemonized")
    parser.add_option('-x', '--exit', default=False, dest='exit_on_worker',
            action='store_true', help="exit on worker failure")
            
    opts, args = parser.parse_args(args)
    if not os.path.exists(opts.config_file):
        print "No config file found at: %s" % opts.config_file
        sys.exit(1)

    ret_opts = Bunch()
    for o in ('daemonize', 'exit_on_worker', 'pidfile', 'config_file'):
        setattr(ret_opts, o, getattr(opts, o))

    return ret_opts
    
    
    
def main(args):
    opts = parse_args(args)
    
    try:
        cbe = CoprBackend(opts.config_file, ext_opts=opts)
        if opts.daemonize:
            daemonize(opts.pidfile)
        cbe.run()
    except Exception, e:
        print 'Killing/Dying'
        if 'cbe' in locals():
            for w in cbe.workers:
                w.terminate()
        raise
    except KeyboardInterrupt, e:
        pass
    
if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception, e:
        print "ERROR:  %s - %s" % (str(type(e)), str(e))
        sys.exit(1)
    except KeyboardInterrupt, e:
        print "\nUser cancelled, may need cleanup\n"
        sys.exit(0)
        

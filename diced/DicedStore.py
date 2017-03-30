"""Functionality to open/close connection to a DVID server.

In addition to connecting to the DICED store, it also provides
top-level exception handling for the package.
"""

from libdvid import DVIDNodeService, DVIDServerService, ConnectionMethod, DVIDConnection
from libdvid._dvid_python import DVIDException
import subprocess
import os
import tempfile

from DicedRepo import DicedRepo
from DicedException import DicedException


class DicedStore(object):
    """Setup and destroy DVID connection or point to pre-existing DVID server.

    TODO:
        Support deletion of created repos.  Support custom launch options, such
        as using groupcache, read-only.
    """

    # constants to configure DVID toml files
    WEBCLIENT = "WEBCLIENT" # local (stored in package)
    LOGNAME = "LOGNAME" # local in application directory
    DBPATH = "DBPATH" # gbucket or local
    PORT = "PORT" 
    RPCPORT = "RPCPORT"
    
    GBUCKET_TOML = """
[server]
httpAddress = ":PORT"
rpcAddress = ":RPCPORT"
webClient = "WEBCLIENT"
instance_id_gen = "sequential"
instance_id_start = 100  # new ids start at least from this.
[logging]
logfile = "LOGNAME"
max_log_size = 500 # MB
max_log_age = 30   # days
[store]
    [store.mutable]
        engine = "gbucket"
        bucket= "DBPATH"
    """

    LEVELDB_TOML = """
[server]
httpAddress = ":PORT"
rpcAddress = ":RPCPORT"
webClient = "WEBCLIENT"
instance_id_gen = "sequential"
instance_id_start = 100  # new ids start at least from this.
[logging]
logfile = "LOGNAME"
max_log_size = 500 # MB
max_log_age = 30   # days
[store]
    [store.default]
        engine = "basholeveldb"
        path = "DBPATH"
    """

    def __init__(self, location, port=8000, rpcport=8001, permissionfile=None, appdir=None):
        """Init.

        The user can start the DVIDStore in three different ways.
        To store data in Google Storage, DVIDStore location should be
        formatted as 'gs://<bucketname>, where the bucket should already
        exist.  This will automatically launch a server on the specified
        local port that will communicate with this storage.  A local
        path can also be specified, instead of a google bucket if very
        large-scale storage in unnecessary.  If the user wants to point
        to a pre-existing DVID server that could have any backend
        storage, the location should be formatted as "dvid://<servername>".

        Note:
            If permissions are needed to access the google bucket, a
            configuration JSON should be pointed to by GOOGLE_APPLICATION_CREDENTIALS
            environment variable or should be passed to this function.

            'dvid' needs to be in the executable path.  This will be setup
            by default if using a conda installation.

            Default DVID ports or specified ports must be available to the program.

        Args:
            location (str): location of DVID server
            port (integer): port that DVID will take http requests
            port (integer): port that DVID will take rpc requests
            permissionfile (str): permission json file location for gbucket
            appdir (str): directory that contains application information
        
        Exceptons:
            Will raise DicedException if DVID server cannot be created or
            if provide address cannot be found.
        """
    
        self._dvidproc = None
        self._server = None

        # if gs or local launch DVID
        gbucket = location.startswith("gs://")
        fileloc = not location.startswith("dvid://") and not gbucket
        if gbucket or fileloc:
            # appdir is '~/.dvidcloudstore' by default
            if appdir is None:
                appdir = '~/.dvidcloudstore'
            appdir = os.path.expanduser(appdir)
            if not os.path.exists(appdir):
                os.makedirs(appdir)
            if not os.path.exists(appdir + "/dvid"):
                os.makedirs(appdir + "/dvid")

            self._server = "127.0.0.1:" + str(port)
            
            # find pre-built console in resources
            import pkg_resources
            consolepath = pkg_resources.resource_filename('diced', 'dvid-console')

            # create dvidlog 
            logfile = tempfile.NamedTemporaryFile(dir=(appdir + "/dvid"),
                    suffix='.log', delete=False)
            logname = logfile.name

            tomldata = None
            if gbucket:
                tomldata = self.GBUCKET_TOML
                tomldata = tomldata.replace(self.DBPATH, location.split("gs://")[1])
            else:
                tomldata = self.LEVELDB_TOML
                tomldata = tomldata.replace(self.DBPATH, location)

            tomldata = tomldata.replace(self.WEBCLIENT, consolepath)
            tomldata = tomldata.replace(self.LOGNAME, logname)
            tomldata = tomldata.replace(self.RPCPORT, str(rpcport))
            tomldata = tomldata.replace(self.PORT, str(port))

            # write toml to temporary file
            tomlfile = tempfile.NamedTemporaryFile(dir=appdir + "/dvid",
                    suffix='.toml', delete=False)
            tomllocation = tomlfile.name
            tomlfile.write(tomldata)
            tomlfile.close()

            # copy environment and set new variable if permissionfile
            local_env = os.environ.copy()
            if permissionfile is not None:
                local_env["GOOGLE_APPLICATION_CREDENTIALS"] = permissionfile 

            self._dvidproc = subprocess.Popen(['dvid', 'serve', tomllocation],
                    env=local_env, stdout=None) 
        else:
            self._server = location + ":" + str(port)
            

        # allow a few seconds for DVID to launch
        if self._dvidproc is not None:
            import time
            print "Establishing connection..."
            time.sleep(10) # wait for connection
        
        # check that dvid server is accepting connections
        try:
            DVIDServerService(self._server)
        except DVIDException, err:
            raise DicedException("DVID connection failed")


    def __del__(self):
        """Shuts down DVID server if user created it.

        This class does not contain references to other objects, so
        this should be safe.  We opted for using __del__ to release
        the database resource; a context manager would be too restrictive.
        Using weak refs is another option.
        """

        if self._dvidproc is not None:
            self._dvidproc.terminate()

    def create_repo(self, name, description=""):
        """Create repo.

        Note:
            DVID does not require unique names but unique names
            will be enforced through this interface.  This will
            simplify access for most common use cases.  In general,
            users should use the web console and specific version
            ids to ensure access to the desired data.

        Args:
            name (str): name of DVID respository (must be unique)
            description (str): description of repository
        """
        try:
            curr_repos = self.list_repos()
            for (reponame, uuid) in curr_repos:
                if reponame == name:
                    raise DicedException("Repo name already exists")
            service = DVIDServerService(_server)
            uuid = service.create_new_repo(name, description)
        except DVIDException, err:
            raise DicedException("Failed to create repo")

    def delete_repo(self, name):
        """Delete entire repo -- this cannot be undone!

        Note:
            For large repos this could be very time-consuming.
            While this is non-blocking, currently, DVID
            will not resume the deletion on restart, so it is
            possible for data to still be stored even if it
            is superficially removed.  For now, the user should
            ensure DicedStore is open for a some time after
            issue the command.

        TODO:
            Implement a restartable background delete.

        """

        uuid = self.get_repouuid(name)

        deletecall = subprocess.Popen(['dvid', 'repos', 'delete', uuid], stdout=None)
        deletecall.communicate()

    def list_repos(self):
        """List all repositories in the store.

        Returns:
            A list of (name, uuid) tuples
        """
        
        data = None 
        try:
            conn = DVIDConnection(_server) 
            data = conn.make_request("/repos/info", ConnectionMethod.GET)
        except DVIDException, err:
            raise DicedException("Failed to access /repos/info on DVID")
        
        jdata = json.loads(data) 
        res = []
        for key, val in jdata:
            res.append((val["Alias"], val["Root"]))

        return res

    def get_repouuid(self, name):
        """Return a repo UUID given the name.

        The root node is taken by default.

        Returns:
            A string representing the UUID.

        Raises:
            Raised DicedException if repo does not exist.
        """
        repos = self.list_repos()
        for (tname, repoid) in repos:
            if tname == name:
                return repoid
        raise DicedException("repo name does not exist")

    def open_repo(self, name=None, uuid=None):
        """Open repository of the specified name (or by unique identifier).

        If only the name is specified, the root node is taken by default.

        Args:
            name (str): name of repository to open
            uuid (str): unique identifier for repository
        
        Returns:
            A repository object if it exists.

        Exception:
            Raises a DicedException if repo does not exist.
        """

        if uuid is not None:
            try:
                ns = DVIDNodeService(_server, uuid)
                return DicedRepo(_server, uuid, self)
            except DVIDException:
                # repo does not exist
                raise DicedException("uuid does not exist")
        elif name is not None:
            repoid = self.get_repouuid(name)
            return DicedRepo(_server, repoid, self)

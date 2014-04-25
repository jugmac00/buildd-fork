# Copyright 2009-2011 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type
__all__ = [
    'BuilddTestCase',
    'FakeSlave',
    ]

import os
import shutil
import tempfile
import unittest
from ConfigParser import SafeConfigParser

from fixtures import EnvironmentVariable
from txfixtures.tachandler import TacTestFixture

from lpbuildd.slave import BuildDSlave


test_conffile = os.path.join(
    os.path.dirname(__file__), 'buildd-slave-test.conf')


class MockBuildManager(object):
    """Mock BuildManager class.

    Only implements 'is_archive_private' as False.
    """
    is_archive_private = False


class BuilddTestCase(unittest.TestCase):
    """Unit tests for logtail mechanisms."""

    def setUp(self):
        """Setup a BuildDSlave using the test config."""
        conf = SafeConfigParser()
        conf.read(test_conffile)
        conf.set("slave", "filecache", tempfile.mkdtemp())

        self.slave = BuildDSlave(conf)
        self.slave._log = True
        self.slave.manager = MockBuildManager()

        self.here = os.path.abspath(os.path.dirname(__file__))

    def tearDown(self):
        """Remove the 'filecache' directory used for the tests."""
        shutil.rmtree(self.slave._cachepath)

    def makeLog(self, size):
        """Inject data into the default buildlog file."""
        f = open(self.slave.cachePath('buildlog'), 'w')
        f.write("x" * size)
        f.close()


class BuilddSlaveTestSetup(TacTestFixture):
    r"""Setup BuildSlave for use by functional tests

    >>> fixture = BuilddSlaveTestSetup()
    >>> fixture.setUp()

    Make sure the server is running

    >>> import xmlrpclib
    >>> s = xmlrpclib.Server('http://localhost:8221/rpc/')
    >>> s.echo('Hello World')
    ['Hello World']
    >>> fixture.tearDown()

    Again for luck !

    >>> fixture.setUp()
    >>> s = xmlrpclib.Server('http://localhost:8221/rpc/')

    >>> s.echo('Hello World')
    ['Hello World']

    >>> info = s.info()
    >>> len(info)
    3
    >>> print info[:2]
    ['1.0', 'i386']

    >>> for buildtype in sorted(info[2]):
    ...     print buildtype
    binarypackage
    debian
    sourcepackagerecipe
    translation-templates

    >>> s.status()["builder_status"]
    'BuilderStatus.IDLE'

    >>> fixture.tearDown()
    """
    def setUpRoot(self):
        """Recreate empty root directory to avoid problems."""
        if os.path.exists(self.root):
            shutil.rmtree(self.root)
        os.mkdir(self.root)
        filecache = os.path.join(self.root, 'filecache')
        os.mkdir(filecache)
        self.useFixture(EnvironmentVariable('HOME', self.root))
        self.useFixture(
            EnvironmentVariable('BUILDD_SLAVE_CONFIG', test_conffile))
        # XXX cprov 2005-05-30:
        # When we are about running it seriously we need :
        # * install sbuild package
        # * to copy the scripts for sbuild
        self.addCleanup(shutil.rmtree, self.root)

    @property
    def root(self):
        return '/var/tmp/buildd'

    @property
    def tacfile(self):
        return os.path.abspath(os.path.join(
            os.path.dirname(__file__),
            os.path.pardir,
            os.path.pardir,
            'buildd-slave.tac'
            ))

    @property
    def pidfile(self):
        return os.path.join(self.root, 'build-slave.pid')

    @property
    def logfile(self):
        return '/var/tmp/build-slave.log'

    def _hasDaemonStarted(self):
        """Called by the superclass to check if the daemon is listening.

        The slave is ready when it's accepting connections.
        """
        # This must match buildd-slave-test.conf.
        return self._isPortListening('localhost', 8221)

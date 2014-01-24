# Copyright 2013 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import os
import shutil
import tempfile
from textwrap import dedent

from testtools import TestCase

from lpbuildd.tests.fakeslave import FakeSlave
from lpbuildd.sourcepackagerecipe import (
    RETCODE_FAILURE_INSTALL_BUILD_DEPS,
    SourcePackageRecipeBuildManager,
    SourcePackageRecipeBuildState,
    )


class MockBuildManager(SourcePackageRecipeBuildManager):
    def __init__(self, *args, **kwargs):
        super(MockBuildManager, self).__init__(*args, **kwargs)
        self.commands = []
        self.iterators = []

    def runSubProcess(self, path, command, iterate=None):
        self.commands.append([path]+command)
        if iterate is None:
            iterate = self.iterate
        self.iterators.append(iterate)
        return 0


class TestSourcePackageRecipeBuildManagerIteration(TestCase):
    """Run SourcePackageRecipeBuildManager through its iteration steps."""
    def setUp(self):
        super(TestSourcePackageRecipeBuildManagerIteration, self).setUp()
        self.working_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.working_dir))
        slave_dir = os.path.join(self.working_dir, 'slave')
        home_dir = os.path.join(self.working_dir, 'home')
        for dir in (slave_dir, home_dir):
            os.mkdir(dir)
        self.slave = FakeSlave(slave_dir)
        self.buildid = '123'
        self.buildmanager = MockBuildManager(self.slave, self.buildid)
        self.buildmanager.home = home_dir
        self.buildmanager._cachepath = self.slave._cachepath
        self.chrootdir = os.path.join(
            home_dir, 'build-%s' % self.buildid, 'chroot-autobuild')

    def getState(self):
        """Retrieve build manager's state."""
        return self.buildmanager._state

    def startBuild(self):
        # The build manager's iterate() kicks off the consecutive states
        # after INIT.
        extra_args = {
            'recipe_text': dedent("""\
                # bzr-builder format 0.2 deb-version {debupstream}-0~{revno}
                http://bazaar.launchpad.dev/~ppa-user/+junk/wakeonlan"""),
            'suite': 'maverick',
            'ogrecomponent': 'universe',
            'author_name': 'Steve\u1234',
            'author_email': 'stevea@example.org',
            'archive_purpose': 'puppies',
            'distroseries_name': 'maverick',
            'archives': [
                'deb http://archive.ubuntu.com/ubuntu maverick main universe',
                'deb http://ppa.launchpad.net/launchpad/bzr-builder-dev/'
                    'ubuntu main',
                ],
            }
        self.buildmanager.initiate({}, 'chroot.tar.gz', extra_args)

        # Skip states that are done in DebianBuildManager to the state
        # directly before BUILD_RECIPE.
        self.buildmanager._state = SourcePackageRecipeBuildState.UPDATE

        # BUILD_RECIPE: Run the slave's payload to build the source package.
        self.buildmanager.iterate(0)
        self.assertEqual(
            SourcePackageRecipeBuildState.BUILD_RECIPE, self.getState())
        expected_command = [
            'buildrecipepath', 'buildrecipe', self.buildid,
            'Steve\u1234'.encode('utf-8'), 'stevea@example.org',
            'maverick', 'maverick', 'universe', 'puppies',
            ]
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled('chrootFail'))

    def test_iterate(self):
        # The build manager iterates a normal build from start to finish.
        self.startBuild()

        log_path = os.path.join(self.buildmanager._cachepath, 'buildlog')
        log = open(log_path, 'w')
        log.write("I am a build log.")
        log.close()

        changes_path = os.path.join(
            self.buildmanager.home, 'build-%s' % self.buildid,
            'foo_1_source.changes')
        changes = open(changes_path, 'w')
        changes.write("I am a changes file.")
        changes.close()

        manifest_path = os.path.join(
            self.buildmanager.home, 'build-%s' % self.buildid, 'manifest')
        manifest = open(manifest_path, 'w')
        manifest.write("I am a manifest file.")
        manifest.close()

        # After building the package, reap processes.
        self.buildmanager.iterate(0)
        expected_command = [
            'processscanpath', 'scan-for-processes', self.buildid,
            ]
        self.assertEqual(
            SourcePackageRecipeBuildState.BUILD_RECIPE, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled('buildFail'))
        self.assertEqual(
            [((changes_path,), {}), ((manifest_path,), {})],
            self.slave.addWaitingFile.calls)

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.buildmanager.iterateReap(self.getState(), 0)
        expected_command = [
            'umountpath', 'umount-chroot', self.buildid
            ]
        self.assertEqual(SourcePackageRecipeBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled('buildFail'))

    def test_iterate_BUILD_RECIPE_install_build_deps_depfail(self):
        # The build manager can detect dependency wait states.
        self.startBuild()

        log_path = os.path.join(self.buildmanager._cachepath, 'buildlog')
        log = open(log_path, 'w')
        log.write(
            "The following packages have unmet dependencies:\n"
            " pbuilder-satisfydepends-dummy : Depends: base-files (>= 1000)"
            " but it is not going to be installed.\n")
        log.close()

        # The buildmanager calls depFail correctly and reaps processes.
        self.buildmanager.iterate(RETCODE_FAILURE_INSTALL_BUILD_DEPS)
        expected_command = [
            'processscanpath', 'scan-for-processes', self.buildid
            ]
        self.assertEqual(
            SourcePackageRecipeBuildState.BUILD_RECIPE, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled('buildFail'))
        self.assertEqual(
            [(("base-files (>= 1000)",), {})], self.slave.depFail.calls)

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.buildmanager.iterateReap(self.getState(), 0)
        expected_command = [
            'umountpath', 'umount-chroot', self.buildid
            ]
        self.assertEqual(SourcePackageRecipeBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled('buildFail'))

    def test_iterate_BUILD_RECIPE_install_build_deps_buildfail(self):
        # If the build manager cannot detect a dependency wait from a
        # build-dependency installation failure, it fails the build.
        self.startBuild()

        log_path = os.path.join(self.buildmanager._cachepath, 'buildlog')
        log = open(log_path, 'w')
        log.write("I am a failing build log.")
        log.close()

        # The buildmanager calls buildFail correctly and reaps processes.
        self.buildmanager.iterate(RETCODE_FAILURE_INSTALL_BUILD_DEPS)
        expected_command = [
            'processscanpath', 'scan-for-processes', self.buildid
            ]
        self.assertEqual(
            SourcePackageRecipeBuildState.BUILD_RECIPE, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertTrue(self.slave.wasCalled('buildFail'))
        self.assertFalse(self.slave.wasCalled('depFail'))

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.buildmanager.iterateReap(self.getState(), 0)
        expected_command = [
            'umountpath', 'umount-chroot', self.buildid
            ]
        self.assertEqual(SourcePackageRecipeBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
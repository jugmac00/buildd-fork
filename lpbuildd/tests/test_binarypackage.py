# Copyright 2013 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import os
import shutil
import tempfile
from textwrap import dedent

from debian.deb822 import PkgRelation
from testtools import TestCase
from testtools.matchers import (
    ContainsDict,
    Equals,
    Is,
    MatchesListwise,
    )
from twisted.internet.task import Clock

from lpbuildd.binarypackage import (
    BinaryPackageBuildManager,
    BinaryPackageBuildState,
    SBuildExitCodes,
    )
from lpbuildd.tests.fakeslave import (
    FakeMethod,
    FakeSlave,
    )


class MockTransport:
    def __init__(self):
        self.loseConnection = FakeMethod()
        self.signalProcess = FakeMethod()


class MockSubprocess:
    def __init__(self, path):
        self.path = path
        self.transport = MockTransport()


class MockBuildManager(BinaryPackageBuildManager):
    def __init__(self, *args, **kwargs):
        super(MockBuildManager, self).__init__(*args, **kwargs)
        self.commands = []
        self.iterators = []
        self.arch_indep = False

    def runSubProcess(self, path, command, iterate=None):
        self.commands.append([path]+command)
        if iterate is None:
            iterate = self.iterate
        self.iterators.append(iterate)
        self._subprocess = MockSubprocess(path)
        return 0


def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)


class TestBinaryPackageBuildManagerIteration(TestCase):
    """Run BinaryPackageBuildManager through its iteration steps."""
    def setUp(self):
        super(TestBinaryPackageBuildManagerIteration, self).setUp()
        self.working_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.working_dir))
        slave_dir = os.path.join(self.working_dir, 'slave')
        home_dir = os.path.join(self.working_dir, 'home')
        for dir in (slave_dir, home_dir):
            os.mkdir(dir)
        self.slave = FakeSlave(slave_dir)
        self.buildid = '123'
        self.clock = Clock()
        self.buildmanager = MockBuildManager(
            self.slave, self.buildid, reactor=self.clock)
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
        self.buildmanager.initiate(
            {'foo_1.dsc': ''}, 'chroot.tar.gz',
            {'distribution': 'ubuntu', 'suite': 'warty',
             'ogrecomponent': 'main'})

        os.makedirs(self.chrootdir)

        # Skip DebianBuildManager states to the state directly before
        # SBUILD.
        self.buildmanager._state = BinaryPackageBuildState.UPDATE

        # SBUILD: Build the package.
        self.buildmanager.iterate(0)
        self.assertState(
            BinaryPackageBuildState.SBUILD,
            [
            'sharepath/slavebin/sbuild-package', 'sbuild-package',
            self.buildid, 'i386', 'warty', '-c', 'chroot:autobuild',
            '--arch=i386', '--dist=warty', '--purge=never', '--nolog',
            'foo_1.dsc',
            ], True)
        self.assertFalse(self.slave.wasCalled('chrootFail'))

    def assertState(self, state, command, final):
        self.assertEqual(state, self.getState())
        self.assertEqual(command, self.buildmanager.commands[-1])
        if final:
            self.assertEqual(
                self.buildmanager.iterate, self.buildmanager.iterators[-1])
        else:
            self.assertNotEqual(
                self.buildmanager.iterate, self.buildmanager.iterators[-1])

    def assertScansSanely(self, exit_code):
        # After building the package, reap processes.
        self.buildmanager.iterate(exit_code)
        self.assertState(
            BinaryPackageBuildState.SBUILD,
            ['sharepath/slavebin/scan-for-processes', 'scan-for-processes',
             self.buildid], False)

    def assertUnmountsSanely(self):
        self.buildmanager.iterateReap(self.getState(), 0)
        self.assertState(
            BinaryPackageBuildState.UMOUNT,
            ['sharepath/slavebin/umount-chroot', 'umount-chroot',
             self.buildid], True)

    def test_iterate(self):
        # The build manager iterates a normal build from start to finish.
        self.startBuild()

        write_file(
            os.path.join(self.buildmanager._cachepath, 'buildlog'),
            "I am a build log.")
        changes_path = os.path.join(
            self.buildmanager.home, 'build-%s' % self.buildid,
            'foo_1_i386.changes')
        write_file(changes_path, "I am a changes file.")

        # After building the package, reap processes.
        self.assertScansSanely(SBuildExitCodes.OK)
        self.assertFalse(self.slave.wasCalled('buildFail'))
        self.assertEqual(
            [((changes_path,), {})], self.slave.addWaitingFile.calls)

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.assertUnmountsSanely()
        self.assertFalse(self.slave.wasCalled('buildFail'))

    def test_abort_sbuild(self):
        # Aborting sbuild kills processes in the chroot.
        self.startBuild()

        # Send an abort command.  The build manager reaps processes.
        self.buildmanager.abort()
        self.assertState(
            BinaryPackageBuildState.SBUILD,
            ['sharepath/slavebin/scan-for-processes', 'scan-for-processes',
             self.buildid], False)
        self.assertFalse(self.slave.wasCalled('buildFail'))

        # If reaping completes successfully, the build manager returns
        # control to the DebianBuildManager in the UMOUNT state.
        self.assertUnmountsSanely()
        self.assertFalse(self.slave.wasCalled('buildFail'))

    def test_abort_sbuild_fail(self):
        # If killing processes in the chroot hangs, the build manager does
        # its best to clean up and fails the builder.
        self.startBuild()
        sbuild_subprocess = self.buildmanager._subprocess

        # Send an abort command.  The build manager reaps processes.
        self.buildmanager.abort()
        self.assertState(
            BinaryPackageBuildState.SBUILD,
            ['sharepath/slavebin/scan-for-processes', 'scan-for-processes',
             self.buildid], False)
        self.assertFalse(self.slave.wasCalled('builderFail'))
        reap_subprocess = self.buildmanager._subprocess

        # If reaping fails, the builder is failed, sbuild is killed, and the
        # reaper is disconnected.
        self.clock.advance(120)
        self.assertTrue(self.slave.wasCalled('builderFail'))
        self.assertEqual(
            [(('KILL',), {})], sbuild_subprocess.transport.signalProcess.calls)
        self.assertNotEqual(
            [], sbuild_subprocess.transport.loseConnection.calls)
        self.assertNotEqual([], reap_subprocess.transport.loseConnection.calls)

        write_file(
            os.path.join(self.buildmanager._cachepath, 'buildlog'),
            "I am a build log.")

        # When sbuild exits, it does not reap processes again, but proceeds
        # directly to UMOUNT.
        self.buildmanager.iterate(128 + 9)  # SIGKILL
        self.assertState(
            BinaryPackageBuildState.UMOUNT,
            ['sharepath/slavebin/umount-chroot', 'umount-chroot',
             self.buildid], True)

    def test_abort_between_subprocesses(self):
        # If a build is aborted between subprocesses, the build manager
        # pretends that it was terminated by a signal.
        self.buildmanager.initiate(
            {'foo_1.dsc': ''}, 'chroot.tar.gz',
            {'distribution': 'ubuntu', 'suite': 'warty',
             'ogrecomponent': 'main'})

        self.buildmanager.abort()
        self.assertState(
            BinaryPackageBuildState.INIT,
            ['sharepath/slavebin/scan-for-processes', 'scan-for-processes',
             self.buildid], False)

        self.buildmanager.iterate(0)
        self.assertState(
            BinaryPackageBuildState.CLEANUP,
            ['sharepath/slavebin/remove-build', 'remove-build', self.buildid],
            True)
        self.assertFalse(self.slave.wasCalled('builderFail'))

    def test_missing_changes(self):
        # The build manager recovers if the expected .changes file does not
        # exist, and considers it a package build failure.
        self.startBuild()
        write_file(
            os.path.join(self.buildmanager._cachepath, 'buildlog'),
            "I am a build log.")
        build_dir = os.path.join(
            self.buildmanager.home, 'build-%s' % self.buildid)
        write_file(
            os.path.join(build_dir, 'foo_2_i386.changes'),
            "I am a changes file.")

        # After building the package, reap processes.
        self.assertScansSanely(SBuildExitCodes.OK)
        self.assertTrue(self.slave.wasCalled('buildFail'))
        self.assertEqual(
            [((os.path.join(build_dir, 'foo_1_i386.changes'),), {})],
            self.slave.addWaitingFile.calls)

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.assertUnmountsSanely()
        self.assertTrue(self.slave.wasCalled('buildFail'))

    def test_getAvailablePackages(self):
        # getAvailablePackages scans the correct set of files and returns
        # reasonable version information.
        apt_lists = os.path.join(self.chrootdir, "var", "lib", "apt", "lists")
        os.makedirs(apt_lists)
        write_file(
            os.path.join(
                apt_lists,
                "archive.ubuntu.com_ubuntu_trusty_main_binary-amd64_Packages"),
            dedent("""\
                Package: foo
                Version: 1.0
                Provides: virt

                Package: bar
                Version: 2.0
                Provides: versioned-virt (= 3.0)
                """))
        write_file(
            os.path.join(
                apt_lists,
                "archive.ubuntu.com_ubuntu_trusty-proposed_main_binary-amd64_"
                "Packages"),
            dedent("""\
                Package: foo
                Version: 1.1
                """))
        write_file(os.path.join(apt_lists, "other"), "some other stuff")
        expected = {
            "foo": set(["1.0", "1.1"]),
            "bar": set(["2.0"]),
            "virt": set([None]),
            "versioned-virt": set(["3.0"]),
            }
        self.assertEqual(expected, self.buildmanager.getAvailablePackages())

    def test_getBuildDepends_arch_dep(self):
        # getBuildDepends returns only Build-Depends for
        # architecture-dependent builds.
        dscpath = os.path.join(self.working_dir, "foo.dsc")
        write_file(
            dscpath,
            dedent("""\
                Package: foo
                Build-Depends: debhelper (>= 9~), bar | baz
                Build-Depends-Indep: texlive-base
                """))
        self.assertThat(
            self.buildmanager.getBuildDepends(dscpath, False),
            MatchesListwise([
                MatchesListwise([
                    ContainsDict({
                        "name": Equals("debhelper"),
                        "version": Equals((">=", "9~")),
                        }),
                    ]),
                MatchesListwise([
                    ContainsDict({"name": Equals("bar"), "version": Is(None)}),
                    ContainsDict({"name": Equals("baz"), "version": Is(None)}),
                    ])]))

    def test_getBuildDepends_arch_indep(self):
        # getBuildDepends returns both Build-Depends and Build-Depends-Indep
        # for architecture-independent builds.
        dscpath = os.path.join(self.working_dir, "foo.dsc")
        write_file(
            dscpath,
            dedent("""\
                Package: foo
                Build-Depends: debhelper (>= 9~), bar | baz
                Build-Depends-Indep: texlive-base
                """))
        self.assertThat(
            self.buildmanager.getBuildDepends(dscpath, True),
            MatchesListwise([
                MatchesListwise([
                    ContainsDict({
                        "name": Equals("debhelper"),
                        "version": Equals((">=", "9~")),
                        }),
                    ]),
                MatchesListwise([
                    ContainsDict({"name": Equals("bar"), "version": Is(None)}),
                    ContainsDict({"name": Equals("baz"), "version": Is(None)}),
                    ]),
                MatchesListwise([
                    ContainsDict({
                        "name": Equals("texlive-base"),
                        "version": Is(None),
                        }),
                    ])]))

    def test_getBuildDepends_missing_fields(self):
        # getBuildDepends tolerates missing fields.
        dscpath = os.path.join(self.working_dir, "foo.dsc")
        write_file(dscpath, "Package: foo\n")
        self.assertEqual([], self.buildmanager.getBuildDepends(dscpath, True))

    def test_relationMatches_missing_package(self):
        # relationMatches returns False if a dependency's package name is
        # entirely missing.
        self.assertFalse(self.buildmanager.relationMatches(
            {"name": "foo", "version": (">=", "1")}, {"bar": set(["2"])}))

    def test_relationMatches_unversioned(self):
        # relationMatches returns True if a dependency's package name is
        # present and the dependency is unversioned.
        self.assertTrue(self.buildmanager.relationMatches(
            {"name": "foo", "version": None}, {"foo": set(["1"])}))

    def test_relationMatches_versioned(self):
        # relationMatches handles versioned dependencies correctly.
        for version, expected in (
            (("<<", "1"), False), (("<<", "1.1"), True),
            (("<=", "0.9"), False), (("<=", "1"), True),
            (("=", "1"), True), (("=", "2"), False),
            ((">=", "1"), True), ((">=", "1.1"), False),
            ((">>", "0.9"), True), ((">>", "1"), False),
            ):
            assert_method = self.assertTrue if expected else self.assertFalse
            assert_method(self.buildmanager.relationMatches(
                {"name": "foo", "version": version}, {"foo": set(["1"])}),
                "%s %s 1 was not %s" % (version[1], version[0], expected))

    def test_relationMatches_multiple_versions(self):
        # If multiple versions of a package are present, relationMatches
        # returns True for dependencies that match any of them.
        for version, expected in (
            (("=", "1"), True),
            (("=", "1.1"), True),
            (("=", "2"), False),
            ):
            assert_method = self.assertTrue if expected else self.assertFalse
            assert_method(self.buildmanager.relationMatches(
                {"name": "foo", "version": version},
                {"foo": set(["1", "1.1"])}))

    def test_relationMatches_unversioned_virtual(self):
        # Unversioned dependencies match an unversioned virtual package, but
        # versioned dependencies do not.
        for version, expected in ((None, True), ((">=", "1"), False)):
            assert_method = self.assertTrue if expected else self.assertFalse
            assert_method(self.buildmanager.relationMatches(
                {"name": "foo", "version": version},
                {"foo": set([None])}))

    def test_analyseDepWait_all_satisfied(self):
        # If all direct build-dependencies are satisfied, analyseDepWait
        # returns None.
        self.assertIsNone(self.buildmanager.analyseDepWait(
            PkgRelation.parse_relations("debhelper, foo (>= 1)"),
            {"debhelper": set(["9"]), "foo": set(["1"])}))

    def test_analyseDepWait_unsatisfied(self):
        # If some direct build-dependencies are unsatisfied, analyseDepWait
        # returns a stringified representation of them.
        self.assertEqual(
            "foo (>= 1), bar (<< 1) | bar (>= 2)",
            self.buildmanager.analyseDepWait(
                PkgRelation.parse_relations(
                    "debhelper (>= 9~), foo (>= 1), bar (<< 1) | bar (>= 2)"),
                {"debhelper": set(["9"]), "bar": set(["1", "1.5"])}))

    def startDepFail(self, error):
        self.startBuild()
        write_file(
            os.path.join(self.buildmanager._cachepath, 'buildlog'),
            "The following packages have unmet dependencies:\n"
            + (" sbuild-build-depends-hello-dummy : Depends: %s\n" % error)
            + "E: Unable to correct problems, you have held broken packages.\n"
            + ("a" * 4096) + "\n"
            + "Fail-Stage: install-deps\n")

    def assertMatchesDepfail(self, error, dep):
        self.startDepFail(error)
        self.assertScansSanely(SBuildExitCodes.GIVENBACK)
        self.assertUnmountsSanely()
        if dep is not None:
            self.assertFalse(self.slave.wasCalled('buildFail'))
            self.assertEqual([((dep,), {})], self.slave.depFail.calls)
        else:
            self.assertFalse(self.slave.wasCalled('depFail'))
            self.assertTrue(self.slave.wasCalled('buildFail'))

    def test_detects_depfail(self):
        # The build manager detects dependency installation failures.
        self.assertMatchesDepfail(
            "enoent but it is not installable", "enoent")

    def test_detects_versioned_depfail(self):
        # The build manager detects dependency installation failures.
        self.assertMatchesDepfail(
            "ebadver (< 2.0) but 3.0 is to be installed", "ebadver (< 2.0)")

    def test_detects_versioned_current_depfail(self):
        # The build manager detects dependency installation failures.
        self.assertMatchesDepfail(
            "ebadver (< 2.0) but 3.0 is installed", "ebadver (< 2.0)")

    def test_uninstallable_deps_analysis_failure(self):
        # If there are uninstallable build-dependencies and analysis can't
        # find any missing direct build-dependencies, the build manager
        # fails the build as it doesn't have a condition on which it can
        # automatically retry later.
        write_file(
            os.path.join(self.buildmanager.home, "foo_1.dsc"),
            dedent("""\
                Package: foo
                Version: 1
                Build-Depends: uninstallable (>= 1)
                """))
        self.startDepFail(
            "uninstallable (>= 1) but it is not going to be installed")
        apt_lists = os.path.join(self.chrootdir, "var", "lib", "apt", "lists")
        os.makedirs(apt_lists)
        write_file(
            os.path.join(apt_lists, "archive_Packages"),
            dedent("""\
                Package: uninstallable
                Version: 1
                """))
        self.assertScansSanely(SBuildExitCodes.GIVENBACK)
        self.assertUnmountsSanely()
        self.assertFalse(self.slave.wasCalled('depFail'))
        self.assertTrue(self.slave.wasCalled('buildFail'))

    def test_uninstallable_deps_analysis_depfail(self):
        # If there are uninstallable build-dependencies and analysis reports
        # some missing direct build-dependencies, the build manager marks
        # the build as DEPFAIL.
        write_file(
            os.path.join(self.buildmanager.home, "foo_1.dsc"),
            dedent("""\
                Package: foo
                Version: 1
                Build-Depends: ebadver (>= 2)
                """))
        self.startDepFail("ebadver (>= 2) but it is not going to be installed")
        apt_lists = os.path.join(self.chrootdir, "var", "lib", "apt", "lists")
        os.makedirs(apt_lists)
        write_file(
            os.path.join(apt_lists, "archive_Packages"),
            dedent("""\
                Package: ebadver
                Version: 1
                """))
        self.assertScansSanely(SBuildExitCodes.GIVENBACK)
        self.assertUnmountsSanely()
        self.assertFalse(self.slave.wasCalled('buildFail'))
        self.assertEqual([(("ebadver (>= 2)",), {})], self.slave.depFail.calls)

    def test_depfail_with_unknown_error_converted_to_packagefail(self):
        # The build manager converts a DEPFAIL to a PACKAGEFAIL if the
        # missing dependency can't be determined from the log.
        self.startBuild()
        write_file(
            os.path.join(self.buildmanager._cachepath, 'buildlog'),
            "E: Everything is broken.\n")

        self.assertScansSanely(SBuildExitCodes.GIVENBACK)
        self.assertTrue(self.slave.wasCalled('buildFail'))
        self.assertFalse(self.slave.wasCalled('depFail'))

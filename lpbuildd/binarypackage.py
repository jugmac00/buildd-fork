# Copyright 2009, 2010 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).


import os
import re

from lpbuildd.debian import DebianBuildManager, DebianBuildState


class SBuildExitCodes:
    """SBUILD process result codes."""
    OK = 0
    DEPFAIL = 1
    GIVENBACK = 2
    PACKAGEFAIL = 3
    BUILDERFAIL = 4


class BuildLogRegexes:
    """Build log regexes for performing actions based on regexes, and extracting dependencies for auto dep-waits"""
    GIVENBACK = [
        ("^E: There are problems and -y was used without --force-yes"),
        ]
    DEPFAIL = {
        "(?P<pk>[\-+.\w]+)\(inst [^ ]+ ! >> wanted (?P<v>[\-.+\w:~]+)\)": "\g<pk> (>> \g<v>)",
        "(?P<pk>[\-+.\w]+)\(inst [^ ]+ ! >?= wanted (?P<v>[\-.+\w:~]+)\)": "\g<pk> (>= \g<v>)",
        "(?s)^E: Couldn't find package (?P<pk>[\-+.\w]+)(?!.*^E: Couldn't find package)": "\g<pk>",
        "(?s)^E: Package '?(?P<pk>[\-+.\w]+)'? has no installation candidate(?!.*^E: Package)": "\g<pk>",
        "(?s)^E: Unable to locate package (?P<pk>[\-+.\w]+)(?!.*^E: Unable to locate package)": "\g<pk>",
        }


class BinaryPackageBuildState(DebianBuildState):
    SBUILD = "SBUILD"


class BinaryPackageBuildManager(DebianBuildManager):
    """Handle buildd building for a debian style binary package build"""

    initial_build_state = BinaryPackageBuildState.SBUILD

    def __init__(self, slave, buildid, **kwargs):
        DebianBuildManager.__init__(self, slave, buildid, **kwargs)
        self._sbuildpath = os.path.join(self._slavebin, "sbuild-package")
        self._sbuildargs = slave._config.get("binarypackagemanager",
                                             "sbuildargs").split(" ")

    def initiate(self, files, chroot, extra_args):
        """Initiate a build with a given set of files and chroot."""

        self._dscfile = None
        for f in files:
            if f.endswith(".dsc"):
                self._dscfile = f
        if self._dscfile is None:
            raise ValueError, files

        self.archive_purpose = extra_args.get('archive_purpose')
        self.distribution = extra_args['distribution']
        self.suite = extra_args['suite']
        self.component = extra_args['ogrecomponent']
        self.arch_indep = extra_args.get('arch_indep', False)
        self.build_debug_symbols = extra_args.get('build_debug_symbols', False)

        super(BinaryPackageBuildManager, self).initiate(
            files, chroot, extra_args)

    def doRunBuild(self):
        """Run the sbuild process to build the package."""
        args = ["sbuild-package", self._buildid, self.arch_tag]
        args.append(self.suite)
        args.extend(self._sbuildargs)
        args.append("--archive=" + self.distribution)
        args.append("--dist=" + self.suite)
        if self.arch_indep:
            args.append("-A")
        if self.archive_purpose:
            args.append("--purpose=" + self.archive_purpose)
        if self.build_debug_symbols:
            args.append("--build-debug-symbols")
        args.append("--architecture=" + self.arch_tag)
        args.append("--comp=" + self.component)
        args.append(self._dscfile)
        self.runSubProcess( self._sbuildpath, args )

    def iterate_SBUILD(self, success):
        """Finished the sbuild run."""
        if success != SBuildExitCodes.OK:
            log_patterns = []
            stop_patterns = [["^Toolchain package versions:", re.M]]

            if (success == SBuildExitCodes.DEPFAIL or
                success == SBuildExitCodes.PACKAGEFAIL):
                for rx in BuildLogRegexes.GIVENBACK:
                    log_patterns.append([rx, re.M])

            if success == SBuildExitCodes.DEPFAIL:
                for rx in BuildLogRegexes.DEPFAIL:
                    log_patterns.append([rx, re.M])

            if log_patterns:
                rx, mo = self.searchLogContents(log_patterns, stop_patterns)
                if mo:
                    if rx in BuildLogRegexes.GIVENBACK:
                        success = SBuildExitCodes.GIVENBACK
                    elif rx in BuildLogRegexes.DEPFAIL:
                        if not self.alreadyfailed:
                            dep = BuildLogRegexes.DEPFAIL[rx]
                            print("Returning build status: DEPFAIL")
                            print("Dependencies: " + mo.expand(dep))
                            self._slave.depFail(mo.expand(dep))
                            success = SBuildExitCodes.DEPFAIL
                    else:
                        success = SBuildExitCodes.PACKAGEFAIL
                else:
                    success = SBuildExitCodes.PACKAGEFAIL

            if success == SBuildExitCodes.GIVENBACK:
                if not self.alreadyfailed:
                    print("Returning build status: GIVENBACK")
                    self._slave.giveBack()
            elif success == SBuildExitCodes.PACKAGEFAIL:
                if not self.alreadyfailed:
                    print("Returning build status: PACKAGEFAIL")
                    self._slave.buildFail()
            elif success >= SBuildExitCodes.BUILDERFAIL:
                # anything else is assumed to be a buildd failure
                if not self.alreadyfailed:
                    print("Returning build status: BUILDERFAIL")
                    self._slave.builderFail()
            self.alreadyfailed = True
            self.doReapProcesses(self._state)
        else:
            print("Returning build status: OK")
            try:
                self.gatherResults()
            except Exception, e:
                self._slave.log("Failed to gather results: %s" % e)
                self._slave.buildFail()
                self.alreadyfailed = True
            self.doReapProcesses(self._state)

    def iterateReap_SBUILD(self, success):
        """Finished reaping after sbuild run."""
        self._state = DebianBuildState.UMOUNT
        self.doUnmounting()

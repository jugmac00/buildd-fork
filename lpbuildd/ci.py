# Copyright 2022 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import os

from six.moves.configparser import (
    NoOptionError,
    NoSectionError,
    )
from twisted.internet import defer

from lpbuildd.debian import (
    DebianBuildManager,
    DebianBuildState,
    )
from lpbuildd.proxy import BuildManagerProxyMixin


RETCODE_SUCCESS = 0
RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201

# These must match the names of `RevisionStatusResult` enumeration items in
# Launchpad.
RESULT_SUCCEEDED = "SUCCEEDED"
RESULT_FAILED = "FAILED"


def _make_job_id(job_name, job_index):
    return f"{job_name}:{job_index}"


class CIBuildState(DebianBuildState):
    PREPARE = "PREPARE"
    RUN_JOB = "RUN_JOB"


class CIBuildManager(BuildManagerProxyMixin, DebianBuildManager):
    """Run CI jobs."""

    backend_name = "lxd"
    initial_build_state = CIBuildState.PREPARE

    @property
    def needs_sanitized_logs(self):
        return True

    def initiate(self, files, chroot, extra_args):
        """Initiate a build with a given set of files and chroot."""
        self.jobs = extra_args["jobs"]
        if not self.jobs:
            raise ValueError("Must request at least one job")
        self.branch = extra_args.get("branch")
        self.git_repository = extra_args.get("git_repository")
        self.git_path = extra_args.get("git_path")
        self.channels = extra_args.get("channels", {})
        self.proxy_url = extra_args.get("proxy_url")
        self.revocation_endpoint = extra_args.get("revocation_endpoint")
        self.proxy_service = None
        self.job_status = {}
        self.apt_repositories = extra_args.get("apt_repositories")
        self.environment_variables = extra_args.get("environment_variables")
        self.plugin_settings = extra_args.get("plugin_settings")

        super().initiate(files, chroot, extra_args)

    def doRunBuild(self):
        """Start running CI jobs."""
        self.proxy_args = self.startProxy()
        if self.revocation_endpoint:
            self.proxy_args.extend(
                ["--revocation-endpoint", self.revocation_endpoint])
        args = list(self.proxy_args)
        for snap, channel in sorted(self.channels.items()):
            args.extend(["--channel", f"{snap}={channel}"])
        if self.branch is not None:
            args.extend(["--branch", self.branch])
        if self.git_repository is not None:
            args.extend(["--git-repository", self.git_repository])
        if self.git_path is not None:
            args.extend(["--git-path", self.git_path])
        try:
            snap_store_proxy_url = self._builder._config.get(
                "proxy", "snapstore")
            args.extend(["--snap-store-proxy-url", snap_store_proxy_url])
        except (NoSectionError, NoOptionError):
            pass
        self.runTargetSubProcess("run-ci-prepare", *args)

    @property
    def current_job(self):
        return self.jobs[self.stage_index][self.job_index]

    @property
    def has_current_job(self):
        try:
            self.current_job
            return True
        except IndexError:
            return False

    def iterate_PREPARE(self, retcode):
        """Finished preparing for running CI jobs."""
        self.stage_index = 0
        self.job_index = 0
        if retcode == RETCODE_SUCCESS:
            pass
        elif (retcode >= RETCODE_FAILURE_INSTALL and
              retcode <= RETCODE_FAILURE_BUILD):
            if not self.alreadyfailed:
                self._builder.log("Preparation failed.")
                self._builder.buildFail()
            self.alreadyfailed = True
        else:
            if not self.alreadyfailed:
                self._builder.builderFail()
            self.alreadyfailed = True
        if self.has_current_job and not self.alreadyfailed:
            self._state = CIBuildState.RUN_JOB
            self.runNextJob()
        else:
            self.stopProxy()
            self.revokeProxyToken()
            self.doReapProcesses(self._state)

    def iterateReap_PREPARE(self, retcode):
        """Finished reaping after preparing for running CI jobs.

        This only happens if preparation failed or there were no jobs to run.
        """
        self._state = DebianBuildState.UMOUNT
        self.doUnmounting()

    @staticmethod
    def _makeJobID(job_name, job_index):
        return f"{job_name}:{job_index}"

    def runNextJob(self):
        """Run the next CI job."""
        args = list(self.proxy_args)
        if self.apt_repositories is not None:
            for repository in self.apt_repositories:
                args.extend(["--apt-repository", repository])
        if self.environment_variables is not None:
            for key, value in self.environment_variables.items():
                args.extend(
                    ["--environment-variable", f"{key}={value}"])
        if self.plugin_settings is not None:
            for key, value in self.plugin_settings.items():
                args.extend(
                    ["--plugin_setting", f"{key}={value}"])
        job_name, job_index = self.current_job
        self.current_job_id = _make_job_id(job_name, job_index)
        args.extend([job_name, str(job_index)])
        self.runTargetSubProcess("run-ci", *args)

    @defer.inlineCallbacks
    def iterate_RUN_JOB(self, retcode):
        """Finished running a CI job.

        This state is repeated for each CI job in the pipeline.
        """
        if retcode == RETCODE_SUCCESS:
            result = RESULT_SUCCEEDED
        else:
            result = RESULT_FAILED
            if (retcode >= RETCODE_FAILURE_INSTALL and
                    retcode <= RETCODE_FAILURE_BUILD):
                self._builder.log("Job %s failed." % self.current_job_id)
                if len(self.jobs[self.stage_index]) == 1:
                    # Single-job stage, so fail straight away in order to
                    # get simpler error messages.
                    if not self.alreadyfailed:
                        self._builder.buildFail()
                    self.alreadyfailed = True
            else:
                if not self.alreadyfailed:
                    self._builder.builderFail()
                self.alreadyfailed = True
        yield self.deferGatherResults(reap=False)
        self.job_status[self.current_job_id]["result"] = result

        self.job_index += 1
        if self.job_index >= len(self.jobs[self.stage_index]):
            # End of stage.  Fail if any job in this stage has failed.
            current_stage_job_ids = [
                _make_job_id(job_name, job_index)
                for job_name, job_index in self.jobs[self.stage_index]]
            if any(
                self.job_status[job_id]["result"] != RESULT_SUCCEEDED
                for job_id in current_stage_job_ids
            ):
                if not self.alreadyfailed:
                    self._builder.log(
                        "Some jobs in %s failed; stopping." %
                        current_stage_job_ids)
                    self._builder.buildFail()
                self.alreadyfailed = True
            self.stage_index += 1
            self.job_index = 0

        if self.has_current_job and not self.alreadyfailed:
            self.runNextJob()
        else:
            self.stopProxy()
            self.revokeProxyToken()
            self.doReapProcesses(self._state)

    def iterateReap_RUN_JOB(self, retcode):
        """Finished reaping after running a CI job.

        This only happens if the job failed or there were no more jobs to run.
        """
        self.iterateReap_PREPARE(retcode)

    def status(self):
        """See `BuildManager.status`."""
        status = super().status()
        status["jobs"] = dict(self.job_status)
        return status

    def gatherResults(self):
        """Gather the results of the CI job that just completed.

        This is called once for each CI job in the pipeline.
        """
        job_status = {}
        output_path = os.path.join("/build", "output", self.current_job_id)
        log_path = "%s.log" % output_path
        if self.backend.path_exists(log_path):
            log_name = "%s.log" % self.current_job_id
            self.addWaitingFileFromBackend(log_path, log_name)
            job_status["log"] = self._builder.waitingfiles[log_name]
        if self.backend.path_exists(output_path):
            for entry in sorted(self.backend.find(
                    output_path, include_directories=False)):
                path = os.path.join(output_path, entry)
                if self.backend.islink(path):
                    continue
                entry_base = os.path.basename(entry)
                name = os.path.join(self.current_job_id, entry_base)
                self.addWaitingFileFromBackend(path, name=name)
                job_status.setdefault("output", {})[entry_base] = (
                    self._builder.waitingfiles[name])

        # Save a file map for this job in the extra status file.  This
        # allows buildd-manager to fetch job logs/output incrementally
        # rather than having to wait for the entire CI job to finish.
        self.job_status[self.current_job_id] = job_status

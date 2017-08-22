# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

from fixtures import EnvironmentVariable
from systemfixtures import FakeProcesses
from testtools import TestCase

from lpbuildd.target.cli import parse_args


class TestRemove(TestCase):

    def test_succeeds(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        args = [
            "remove-build",
            "--backend=chroot", "--series=xenial", "--arch=amd64", "1",
            ]
        parse_args(args=args).operation.run()

        expected_args = [["sudo", "rm", "-rf", "/expected/home/build-1"]]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

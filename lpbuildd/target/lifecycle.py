# Copyright 2009-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import logging

from lpbuildd.target.backend import BackendException
from lpbuildd.target.operation import Operation


logger = logging.getLogger(__name__)


class Create(Operation):

    description = "Create the target environment."

    @classmethod
    def add_arguments(cls, parser):
        super(Create, cls).add_arguments(parser)
        parser.add_argument("tarball_path", help="path to chroot tarball")

    def run(self):
        logger.info("Creating target for build %s", self.args.build_id)
        self.backend.create(self.args.tarball_path)
        return 0


class Start(Operation):

    description = "Start the target environment."

    def run(self):
        logger.info("Starting target for build %s", self.args.build_id)
        self.backend.start()
        return 0


class Stop(Operation):

    description = "Stop the target environment."

    def run(self):
        logger.info("Stopping target for build %s", self.args.build_id)
        try:
            self.backend.stop()
        except BackendException:
            logger.exception('Failed to stop target')
            return 1
        return 0


class Remove(Operation):

    description = "Remove the target environment."

    def run(self):
        logger.info("Removing build %s", self.args.build_id)
        self.backend.remove()
        return 0

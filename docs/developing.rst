Developing
**********

First of all, it is recommended that you create an lxc container, since the
following steps will make changes in your system. And since some build types
will only work with virtualized containers, creating an lxc vm is the best way
to go. If you just want to run the test suite, creating a container is
sufficient.

You can create a container with the following command:

.. code:: bash

        lxc launch --vm ubuntu:18.04 lp-builddev

Note that you may want to have a profile to share the source code with the
container before running the above command.

Then, inside the container, install the necessary dependencies:

.. code:: bash

        sudo apt-get update
        cat system-dependencies.txt | sudo xargs apt-get install -y

This should be enough for you to be able to run `make check`, which runs the
test suite both in python2 and python3.

More information on how to integrate it with Launchpad can be found here:
https://dev.launchpad.net/Soyuz/HowToDevelopWithBuildd


Creating a changelog entry
--------------------------

In order to create a changelog entry in ``debian/changelog``,
you need to run ``dch`` from the ``devscripts`` package.

.. code:: bash

    dch

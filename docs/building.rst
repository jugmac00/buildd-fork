Building the project
********************

In order to build the package you need ``dpkg-dev`` and ``fakeroot``.

To build the package, do:

.. code:: bash

    debian/rules package
    dpkg-buildpackage -rfakeroot -b

It will "fail" because the package built in the "wrong" place.
Don't worry about that.

To clean up, do:

.. code:: bash

    fakeroot debian/rules clean
    rm launchpad-buildd*deb
    rm ../launchpad-buildd*changes

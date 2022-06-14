# Copyright 2009-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

all: deb

src: clean
	dpkg-buildpackage -rfakeroot -uc -us -S

deb: clean
	dpkg-buildpackage -rfakeroot -uc -us

clean:
	fakeroot debian/rules clean

realclean:
	rm -f ../launchpad-buildd*tar.gz
	rm -f ../launchpad-buildd*dsc
	rm -f ../launchpad-buildd*deb
	rm -f ../launchpad-buildd*changes

.PHONY: all clean deb

check:
	PYTHONPATH=$(CURDIR):$(PYTHONPATH) python3 -m testtools.run \
		discover -v

checkone:
	PYTHONPATH=$(CURDIR):$(PYTHONPATH) python3 -m unittest discover -s lpbuildd -p "test_run_ci.py"
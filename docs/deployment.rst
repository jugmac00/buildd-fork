Production deployment notes
***************************

In Launchpad's production build farm, launchpad-buildd is deployed via base
virtual machine images constructed by taking standard Ubuntu cloud images
and installing launchpad-buildd in them from
https://launchpad.net/~canonical-is-sa/+archive/ubuntu/buildd.  This is done
by
https://code.launchpad.net/~canonical-sysadmins/canonical-is-charms/launchpad-buildd-image-modifier
(currently private, sorry).

At present (November 2020), most of these base VM images are built from
Ubuntu bionic, and launchpad-buildd runs on Python 3.  However, it's
necessary to support the powerpc architecture until at least April 2021 (end
of standard maintenance for xenial), and the powerpc base images need to
stay on xenial since that architecture is no longer supported by bionic;
furthermore, the version of Twisted in xenial has some bugs that break
launchpad-buildd when running on Python 3.  As a result, launchpad-buildd
must run on both Python 2 and 3 for the time being.
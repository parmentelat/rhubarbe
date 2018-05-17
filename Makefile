########## for uploading onto pypi
# this assumes you have an entry 'pypi' in your .pypirc
# see pypi documentation on how to create .pypirc

LIBRARY = rhubarbe

VERSION = $(shell python3 -c "from $(LIBRARY).version import __version__; print(__version__)")
VERSIONTAG = $(LIBRARY)-$(VERSION)
GIT-TAG-ALREADY-SET = $(shell git tag | grep '^$(VERSIONTAG)$$')
# to check for uncommitted changes
GIT-CHANGES = $(shell echo $$(git diff HEAD | wc -l))

# run this only once the sources are in on the right tag
pypi:
	@if [ $(GIT-CHANGES) != 0 ]; then echo "You have uncommitted changes - cannot publish"; false; fi
	@if [ -n "$(GIT-TAG-ALREADY-SET)" ] ; then echo "tag $(VERSIONTAG) already set"; false; fi
	@if ! grep -q ' $(VERSION)' CHANGELOG.md ; then echo no mention of $(VERSION) in CHANGELOG.md; false; fi
	@echo "You are about to release $(VERSION) - OK (Ctrl-c if not) ? " ; read _
	git tag $(VERSIONTAG)
	./setup.py sdist upload -r pypi

# it can be convenient to define a test entry, say testpypi, in your .pypirc
# that points at the testpypi public site
# no upload to build.onelab.eu is done in this case
# try it out with
# pip install -i https://testpypi.python.org/pypi $(LIBRARY)
# dependencies need to be managed manually though
testpypi:
	./setup.py sdist upload -r testpypi

##########
pyfiles:
	@git ls-files | grep '\.py$$' 

pep8:
	$(MAKE) pyfiles | xargs flake8 --max-line-length=80 --exclude=__init__.py

pylint:
	$(MAKE) pyfiles | xargs pylint


.PHONY: pep8 pylint pyfiles
##############################
tags:
	git ls-files | xargs etags

.PHONY: tags
############################## for deploying before packaging
# default is to mess with our preplab and let the production
# site do proper upgrades using pip3
deployment ?= preplab

ifeq "$(deployment)" "production"
    DEST=faraday.inria.fr
else
    DEST=preplab.pl.sophia.inria.fr
endif

# installing in /tmp/rhubarbe-sync for testing
sync:
	@echo 'export PYTHONPATH=/tmp/rhubarbe-sync; alias rhu=/tmp/rhubarbe-sync/bin/rhubarbe'
	rsync -av --relative $$(git ls-files) root@$(DEST):/tmp/rhubarbe-sync/

both: preplab faraday

faraday:
	$(MAKE) sync deployment=production

preplab:
	$(MAKE) sync deployment=preplab

.PHONY: sync both faraday preplab

########## actually install
infra:
	apssh -t r2lab.infra pip3 install --upgrade rhubarbe
	ssh root@faraday.inria.fr systemctl restart monitor
	ssh root@faraday.inria.fr systemctl restart accountsmanager
check:
	apssh -t r2lab.infra rhubarbe version

.PHONY: infra check

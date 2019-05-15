# This script parses the Pipfile and replaces all ssh references to git.collmot.com
# with the equivalent http references, then removes Pipfile.lock
#
# This is useful to make it easier to use this repo in Heroku or Docker because
# you won't need to have ssh in the container to install things. (But you need to
# provide the username and the password by other means, e.g., ~/.netrc).

###############################################################################

cat Pipfile | sed -e 's/ssh:..git@git.collmot.com/https:\/\/git.collmot.com/g' >Pipfile.new
mv Pipfile.new Pipfile
rm Pipfile.lock     # let pipenv regenerate it


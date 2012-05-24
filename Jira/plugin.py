###
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

###

import supybot.dbi as dbi
import supybot.utils as utils
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks

import pyjira
from pyjira import types
from jiranemo import jiracfg

import json
import urllib2
from urlparse import urljoin

class IssueRecord(dbi.Record):
    __fields__ = [
        ('issuekey', eval),
        ]

class DbiJiraDB(plugins.DbiChannelDB):
    class DB(dbi.DB):
        Record = IssueRecord
        def set(self, id, issuekey):
            record = self.Record(issuekey=issuekey)
            super(self.__class__, self).set(1, record)
        def get(self, id=1):
            try:
                return super(self.__class__, self).get(id)
            except:
                return self.Record(issuekey='')

JIRADB = plugins.DB('JIRA', {'flat': DbiJiraDB})

class Jira(callbacks.Plugin):
    """This is a plugin for controlling a Jira instance."""
    threaded = True
    _jiraclient = None

    def __init__(self, irc):
        super(self.__class__, self).__init__(irc)
        self.db = JIRADB()

    @property
    def jclient(self):
        if self._jiraclient is None:
          cfg = jiracfg.JiraConfiguration(readConfigFiles=False)
          cfg.user = self.registryValue('username')
          cfg.password = self.registryValue('password')
          cfg.wsdl = self.registryValue('uri') + "/rpc/soap/jirasoapservice-v2?wsdl"
          authorizer = pyjira.auth.CachingInteractiveAuthorizer(cfg.authCache)
          ccAuthorizer = pyjira.auth.CookieCachingInteractiveAuthorizer(cfg.cookieCache)
          self._jiraclient = pyjira.JiraClient(cfg.wsdl, (cfg.user, cfg.password), 
                     authorizer=authorizer, webAuthorizer=ccAuthorizer)
        return self._jiraclient

    def assign(self, irc, msg, args, key, assignee):
        """<issue> <assignee>

        Assign an issue to a user (NOTE: this requires their jira username, not their IRC nick!)"""

        if key == '.':
            key = self.db.get(msg.args[0], 1).issuekey
            if not key:
                irc.reply("No previous issue found")
                return
        self.db.set(msg.args[0], 1, key)

        self.log.info("Setting assignee of %s to %s" % (key, assignee))
        self.jclient.updateIssue(key, "assignee", assignee)
        irc.replySuccess()

    assign = wrap(assign, ['somethingWithoutSpaces', 'somethingWithoutSpaces'])

    def benefit(self, irc, msg, args, key, b):
        """<issue> [ Low | Medium | High ]

        Specify the Benefit for an issue (NOTE: this is a Euca-specific custom field)"""

        if key == '.':
            key = self.db.get(msg.args[0], 1).issuekey
            if not key:
                irc.reply("No previous issue found")
                return
        self.db.set(msg.args[0], 1, key)

        self.log.info("Setting benefit of %s to %s" % (key, b))
        self.jclient.updateIssue(key, "Benefit", b)
        irc.replySuccess()

    benefit = wrap(benefit, ['somethingWithoutSpaces', 'somethingWithoutSpaces'])

    def target(self, irc, msg, args, key, version):
        """<issue> <version> ...

        Specify the target version(s) for an issue, separated by spaces.
        (NOTE: this is a Euca-specific custom field)"""

        if key == '.':
            key = self.db.get(msg.args[0], 1).issuekey
            if not key:
                irc.reply("No previous issue found")
                return
        self.db.set(msg.args[0], 1, key)

        proj = key.split('-')[0]
        versions = [ x for x in self.jclient.restclient.get_versions(proj) if x['name'] in version.split() ]
        # TODO: ensure all versions are accounted for
        versionIds = [ x['id'] for x in versions ]
        self.log.info("Setting target of %s to %s ( %s )" % (key, repr(versionIds), repr(version)))
        self.jclient.updateIssue(str(key), "Target Version/s", versionIds)
        irc.reply("Set target version for %s to %s ( %s )" % (key, repr(versionIds), repr(version)))

    target = wrap(target, ['somethingWithoutSpaces', 'text'])

    def addversion(self, irc, msg, args, proj, name):
        """<projectKey> <version>

        Add a version to a project"""

        self.jclient.restclient.add_version(proj, name)
        irc.replySuccess()

    addversion = wrap(addversion, ['somethingWithoutSpaces', 'somethingWithoutSpaces'])

    def current(self, irc, msg, args):
        key = self.db.get(msg.args[0], 1).issuekey
        if not key:
            irc.reply("No previous issue found")
            return
        irc.reply("Current issue is " + key)

    current = wrap(current, [])

    def getversions(self, irc, msg, args, proj):
        """<projectKey>

        List a project's versions"""

        irc.reply("Current versions in %s: %s" % (proj, ", ".join([ x['name'] for x in self.jclient.restclient.get_versions(proj) ])))

    getversions = wrap(getversions, ['somethingWithoutSpaces'])

    def wf(self, irc, msg, args, key, action):
        """<issue>  [ <transition> | list ]

        list: List the valid actions from this state
        <transition>: Perform the specified action.  Abbreviations are allowed and are case insensitive."""

        if key == '.':
            key = self.db.get(msg.args[0], 1).issuekey
            if not key:
                irc.reply("No previous issue found")
                return
        self.db.set(msg.args[0], 1, key)

        actions = [ x['name'] for x in self.jclient.getAvailableActions(key) ]
        if action == "list":
            irc.reply("Available actions: " + ", ".join(actions))
            return

        matches = [ x for x in actions if x.lower().startswith(action.lower()) ]
        if len(matches) == 0:
            irc.reply("No matching actions.  Possible actions: ", ", ".join(actions))
        if len(matches) == 1:
            self.log.info("Attempting to %s for %s" % (matches[0], key))
            self.jclient.progressWorkflowAction(key, matches[0], {})
            status = self.jclient.restclient.get_issue(key)['fields']['status']['name']
            irc.reply("%s now has status '%s'" % ( key, status))
        else:
            irc.reply("workflow action '%s' is ambiguous.  Possible matches: %s" % (action, ", ".join(matches)))

    wf = wrap(wf, ['somethingWithoutSpaces', 'text'])

    def getissue(self, irc, msg, args, key):
        """<issue>

        Display information about an issue in Jira along with a link to
        it on the web."""

        self.db.set(msg.args[0], 1, key)
        try:
            response_json = self.jclient.restclient.get_issue(key)
        except urllib2.HTTPError as e:
            if str(e.code).startswith('4'):
                irc.error('issue {0} does not exist.'.format(key))
            else:
                irc.error('failed to retrieve issue data')
            return
        except ValueError:
            self.log.error('Response from server is not JSON: ' + response_content)
            irc.error('failed to retrieve issue data')
            return
        if 'key' in response_json:
            key = response_json['key']
        else:
            self.log.error("Response lacks an issue key: " + response_content)
            irc.error('failed to retrieve issue data')
            return

        fields = response_json.get('fields', '')
        msg_bits = ['Issue']
        issue_flags = []
        msg_bits.append(key)
        if fields:
            issue_flags.append(fields['status']['name'])
        if issue_flags:
            msg_bits.append('(' + ', '.join(issue_flags) + ')')
        msg_bits[-1] += ':'
        msg_bits.append(fields.get('summary', '(no summary)'))
        msg_bits.append('-')
        msg_bits.append(urljoin(self.jclient.webclient.baseUrl, '/browse/{0}'.format(key)))
        irc.reply(' '.join(msg_bits))

    getissue = wrap(getissue, ['text'])

Class = Jira

#!/usr/bin/python

"""
(c)2010 Ed Marczak (marczak@radiotope.com)
MacTech Conference Bot
v1.4
2010-01-10: Support config file for auth token values.
2010-20-09: Switch to tweepy lib, support OAuth, improve error handling,
            watch rate limit and take appropriate action.
2010-30-07: Add timeout support for Twitter functions
2010-06-01: Initial version
"""

import ConfigParser
import datetime
import os
import re
import sys
import time
import tweepy
from optparse import OptionParser

_FN_TIMEOUT = 22

class Error(Exception):
  """Generic error class."""
  pass


class MTCBotError(Error):
  """Error class for MTCBot."""
  pass


class Config:
  """Read and store config.

  Args:
    path: String with alternate config file location.
  """

  def __init__(self, path = None):
    # Look for config file in common locations, or use supplied path.
    if path is not None:
      self.configfile = path
    else:
      if os.path.exists(os.path.expanduser('~/.mtcbot/config')):
        self.configfile = os.path.expanduser('~/.mtcbot/config')
      elif os.path.exists('/etc/mtcbot/config'):
        self.configfile = '/etc/mtcbot/config'
      else:
        raise MTCBotError('Config file not found.')

    debug_print('Using config file at %s' % self.configfile)
    self.oauthkeys = {}
    config = ConfigParser.SafeConfigParser()
    config.read(self.configfile)
    for i in config.items('Keys'):
      self.oauthkeys[i[0]] = i[1]


def debug_print(msg):
  """Quick and dirty stdout printing with timestamp."""
  debug_enabled = True
  t = time.localtime()
  timestr = ('%02d-%02d-%02d %02d:%02d:%02d' %
            (t[0], t[1], t[2], t[3], t[4], t[5]))
  if debug_enabled:
    print '%s %s' % (timestr, msg)


class MTCBackoff():
  """Global reference for current backoff amount."""

  def __init__(self):
    self.backoff = 0
    self.base_rest_time = 29
    self.rest_time = self.base_rest_time

  def get_backoff(self):
    """Getter for backoff."""
    return self.backoff

  def set_backoff(self, backoff):
    """Setter for backoff."""
    self.backoff = backoff


def MTCBotRest(backoff):
  """Sleep for default time, plus additional backoff."""
  total_time = backoff.rest_time + backoff.backoff
  if total_time > 120:
    total_time = 120
  debug_print('Sleeping for %s' % total_time)
  while total_time > 0:
    sys.stdout.write('\033[1G')
    sys.stdout.write('%s ' % total_time)
    sys.stdout.flush()
    time.sleep(1)
    total_time -= 1
  sys.stdout.write('\033[1G              \033[1G')
  backoff.set_backoff(backoff.get_backoff() + 3)


class Followers:
  """Class to maintain followers."""

  def __init__(self, api):
    self.api = api
    self.followers_ids = []
    self.friends_ids = []
    self.num_followers = 0
    self.num_friends = 0
    self.tempignore = {}

  def get_followers(self):
    """Retrieve followers by ID."""
    self.followers_ids = self.api.followers_ids()
    self.num_followers = len(self.followers_ids)
    return self.followers_ids

  def get_friends(self):
    """Retrieve friends by ID."""
    self.friends_ids = self.api.friends_ids()
    self.num_friends = len(self.friends_ids)
    return self.friends_ids

  def get_num_followers(self):
    """Convenience method for retrieving follower count."""
    return self.num_followers

  def get_num_friends(self):
    """Convenience method for retrieving friend count."""
    return self.num_friends

  def sync(self):
    """Sync friends to followers."""
    # Get who follows us.
    follower_ids = self.get_followers()
    # Get who we follow.
    friends_ids = self.get_friends()

    # Compare
    for f in follower_ids:
      if (f not in friends_ids and
          f not in self.tempignore):
        debug_print('Following user %s.' % f)
        try:
          theuser = self.api.create_friendship(f)
        except tweepy.TweepError, e:
          pattern = 'already requested to follow'
          if re.search(pattern, e.reason):
            self.tempignore[f] = datetime.datetime.now()
            debug_print('Temporarily ignoring user %s' % f)
          else:
            debug_print('TweepError: %s' % e.reason)
      elif f in self.tempignore:
        debug_print('Still ignoring %s.' % f)


def CheckDM(api):
  """Check for and post direct messages."""
  debug_print('Checking for direct messages')
  for message in tweepy.Cursor(api.direct_messages).items():
    debug_print('Posting %s: %s' % (message.sender_screen_name, message.text))
    api.update_status('%s: %s' % (message.sender_screen_name, message.text))
    # We really want to nuke this if we posted it
    api.destroy_direct_message(message.id)


def main():
  parser = OptionParser()
  parser.add_option('--no-followsync',
                    dest = 'followsync',
                    default = False,
                    action = 'store_false',
		    help = r'Don\'t ever sync followers')
  parser.add_option('--no-dm',
                    dest = 'directmessages',
                    default = False,
                    action = 'store_false',
		    help='Don\'t check and retweet direct messages')

  (options, args) = parser.parse_args()

  config = Config()
  backoff = MTCBackoff()
  # Init the API and sign in
  api = False
  while not api:
    try:
      auth = tweepy.OAuthHandler(config.oauthkeys['consumer_key'],
                                 config.oauthkeys['consumer_secret'])
      auth.set_access_token(config.oauthkeys['access_key'],
                            config.oauthkeys['access_secret'])
      api = tweepy.API(auth)
    except:
      debug_print('Could not get auth - Will retry.')
      MTCBotRest(backoff.get_backoff())

  last_check = 0
  followers = Followers(api)
  while True:
    # Main run loop.
    try:
      rate_limit = api.rate_limit_status()
    except:
      debug_print('*** Failed rate limit check - twitter error ***')
      MTCBotRest(backoff)
      continue
    debug_print('Rate limit: %d/%d' % (rate_limit['remaining_hits'],
                                       rate_limit['hourly_limit']))
    # Ensure we're not too close to the hourly limit.
    if rate_limit['remaining_hits'] < 12:
      backoff.rest_time = backoff.base_rest_time * 2
    else:
      backoff.rest_time = backoff.base_rest_time

    # Compute current time and seconds until rate limit reset.
    t = datetime.datetime.now()
    epoch_seconds = time.mktime(t.timetuple())
    lastcheck_time = epoch_seconds - last_check
    secs_to_reset = rate_limit['reset_time_in_seconds'] - epoch_seconds

    # If we've hit the rate limit, sleep until reset.
    if rate_limit['remaining_hits'] < 2:
      debug_print('*** Hit the rate limit!!! ***')
      backoff.rest_time = secs_to_reset
      MTCBotRest(backoff)

    # Only check followers once every 30 min as it damages the rate limit.
    if (epoch_seconds > rate_limit['reset_time_in_seconds'] or
        lastcheck_time > 1800):
      debug_print('*** Syncing followers ***')
      try:
        followers.sync()
        follow_sync = 1
      except:
        debug_print('*** Missed sync - twitter error ***')
        follow_sync = 0
      if follow_sync:
        # Update last check only if there was a good sync.
        t = datetime.datetime.now()
        last_check = time.mktime(t.timetuple())
    else:
      next_sync = (secs_to_reset if secs_to_reset < lastcheck_time else
                   1800 - lastcheck_time)
      debug_print('Skipping follower sync - reset in %d.' % next_sync)

    if not options['directmessages']:
      try:
        CheckDM(api)
      except:
        debug_print('*** Missed DM Check - twitter error ***')
    else:
      debug_print('Skipped checking DM due to command line switch.')

    # If we made it this far, reset backoff.
    backoff.set_backoff(0)
    debug_print('Sleeping - Zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz')
    MTCBotRest(backoff)


if __name__ == '__main__':
  main()


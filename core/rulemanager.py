import logging
import signal
import json

from functools import partial, wraps
from core.rule import Rule
from configuration import config


class RuleManager():

    """
    Class RuleManager
    Main manager class for rule functions
    """

    def __init__(self):

        # Initialize logger
        self.logger = logging.getLogger(__name__)
        self.logger.debug("Initializing the Rule Manager.")

        self.rules = None
        self.conditions = None
        self.ruleSequence = None

    def __signalHandler(self, signum, frame):
        """
        Collector.__signalHandler
        Raise an exception when a signal SIGALRM was received
        """

        raise TimeoutError("Metric calculation has timed out.")

    def loadRules(self, ruleModule, conditionModule, ruleMapFile, ruleSequenceFile):
        """Loads the rules.

        Parameters
        ----------
        ruleModule : module
            A module containing all the rule handling functions.
        ruleMapFile : `str`
            The path for a JSON file defining which rules to run, their order, and their options.
        """

        # Load the Python scripted rules and conditions
        self.rules = ruleModule
        self.conditions = conditionModule

        # Load the configured sequence of rules
        rule_desc = None    # Rule configuration
        rule_seq = None     # Rule order

        try:
            with open(ruleMapFile) as rule_file:
                rule_desc = json.load(rule_file)
        except IOError:
            raise IOError("The rulemap %s could not be found." % ruleMapFile)

        try:
            with open(ruleSequenceFile) as order_file:
                rule_seq = json.load(order_file)
        except IOError:
            raise IOError("The rule sequence file %s could not be found." % ruleSequenceFile)

        # Get the rule from the map
        try:
            self.ruleSequence = [rule_desc[x] for x in rule_seq]
        except KeyError as exception:
            raise ValueError("The rule %s could not be found in the configured rule map %s." % 
                (exception.args[0], ruleMapFile))

        # Check if the rules are valid
        self.__checkRuleSequence(self.ruleSequence)

    def __checkRuleSequence(self, sequence):
        """
        Def RuleManager.__checkRuleSequence
        Checks validity of the configured rule sequence
        """

        # Check each rule that it exists & is a callable Python function
        for item in sequence:

            # Check if the rule exists
            try:
                rule, timeout = self.getRule(item)
            except AttributeError:
                raise NotImplementedError(
                    "Python rule for configured sequence item %s does not exist." %
                    item)

            # The rule must be callable (function) too
            if not callable(rule.call):
                raise ValueError(
                    "Python rule for configured sequence item %s is not callable." %
                    item)

    def bindOptions(self, definitions, item):
        """
        Def RuleManager.bindOptions
        Binds options to a function call
        """

        def invert(f):
            @wraps(f)
            def g(*args, **kwargs):
                return not f(*args, **kwargs)
            return g

        # Invert the boolean result from the policy
        if (definitions == self.conditions) and item["functionName"].startswith("!"):
            return partial(invert(getattr(definitions, item["functionName"][1:])), item["options"])
        else:
            return partial(getattr(definitions, item["functionName"]), item["options"])

    def getRule(self, rule):
        """
        Def RuleManager.getRule
        Returns specific rule from name and its execution timeout
        """

        # Bind the rule options to the function call
        # There may be multiple conditions defined per rule
        rule_obj = Rule(
            self.bindOptions(self.rules, rule),
            map(lambda x: self.bindOptions(self.conditions, x), rule["conditions"])
        )

        # Get timeout from rule-specific config or from default value
        timeout = rule.get("timeout") or config["DEFAULT_RULE_TIMEOUT"]

        return (rule_obj, timeout)

    def sequence(self, items):
        """
        Def RuleManager.sequence
        Runs the sequence of rules on the given file list.

        Parameters
        ----------
        items
            An iterable collection of objects that can be processed by the loaded rules.
        """

        total = len(items)

        # Items can be SDSFiles or metadata (XML) files
        for i, item in enumerate(items):

            self.logger.info("Processing item %s (%d/%d)." % (item.filename, i, total))

            # Get the sequence of rules to be applied
            for rule, timeout in map(self.getRule, self.ruleSequence):

                # Set a signal
                signal.signal(signal.SIGALRM, self.__signalHandler)
                signal.alarm(timeout)

                # Rule options are bound to the call
                try:
                    rule.apply(item)
                    self.logger.info("%s: Successfully executed rule '%s'." % (item.filename, rule.call.func.__name__))

                # The rule was timed out
                except TimeoutError:
                    self.logger.warning("%s: Timeout calling rule '%s'." % (item.filename, rule.call.func.__name__))

                # Policy assertion errors
                except AssertionError as e:
                    self.logger.info("%s: Not executing rule '%s'. Rule did not pass policy '%s'." % (item.filename, rule.call.func.__name__, e))

                # Other exceptions
                except Exception as e:
                    self.logger.error("%s: Rule execution '%s' failed: %s" % (item.filename, rule.call.func.__name__, e), exc_info=False)

                # Disable the alarm
                finally:
                    signal.alarm(0)

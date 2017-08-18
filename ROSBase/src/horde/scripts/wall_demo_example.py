"""Runs the demo that learns to avoid walls.

This module describes an agent that learns to avoid walls. It specifies
the agent's learning algorithm, parameters, policies, features, and
actions. The module also interfaces with the :doc:`learning_foreground`
and the :doc:`action_manager` to run the main learning loop and publish
actions respectively.

All parameters are set in ``if __name__ == "__main__"``

Authors:
    Michele Albach, Shibhansh Dohare, Banafsheh Rafiee,
    Parash Rahman, Niko Yasui.
"""
from __future__ import division

import multiprocessing as mp
import random
from multiprocessing import Value

import numpy as np
import rospy
from geometry_msgs.msg import Twist, Vector3
from scipy import optimize

import tools
from action_manager import start_action_manager
from greedy_gq import GreedyGQ
from gtd import GTD
from to_gtd import TOGTD
from gvf import GVF
from learning_foreground import start_learning_foreground
from policy import Policy
from state_representation import StateConstants
from wis_gtd import WISGTD
from wis_to_gtd import WISTOGTD


class MaximumEntropyMellowmax(Policy):
    """Adaptive softmax policy.

    Action selection is based on maintaining a 
    :py:attr:`~policy.Policy.pi` array which holds action selection
    probabilities. See "An Alternative Softmax Operator for
    Reinforcement Learning" by Asadi and Littman at
    https://arxiv.org/abs/1612.05628. 

    Args:
        omega (float): Hyperparameter for mellowmax.
        value_function: A function used by the policy to update values 
            of :py:attr:`~policy.Policy.pi`. This is usually a value 
            function learned by a GVF.
        feature_indices (numpy array of bool): Indices of the feature
            vector corresponding to indices used by the
            :py:obj:`value_function`.
        action_space (numpy array of action): Numpy array containing
            all actions available to any agent.
    """

    def __init__(self,
                 omega,
                 value_function,
                 feature_indices,
                 action_space,
                 *args, **kwargs):
        kwargs['value_function'] = value_function
        kwargs['feature_indices'] = feature_indices
        kwargs['action_space'] = action_space
        Policy.__init__(self, *args, **kwargs)

        self.mellowmax = lambda x: np.log(np.mean(np.exp(omega * x))) / omega

    def update(self, phi, *args, **kwargs):
        """Updates :py:attr:`~policy.Policy.pi`.

        Uses ``mellowmax`` to specify a root-finding problem that solves
        for a value of ``beta``, the softmax hyperparameter. For
        performance reasons, ``beta`` is bounded between -10 and 10. If
        ``beta`` is consistently on the boundary then the boundary 
        should be changed.
        """
        phi = phi[self.feature_indices]

        q_fun = np.vectorize(lambda action: self.value_function(phi, action))
        q_values = q_fun(self.action_space)

        mm = self.mellowmax(q_values)
        diff = q_values - mm

        beta = optimize.brentq(self.find_beta(diff), -10, 10)
        if beta == 10 or beta == -10:
            rospy.logwarn("Beta = {}".format(beta))
        beta_q = beta * q_values
        self.pi = tools.softmax(beta_q)

    @staticmethod
    def find_beta(diff):
        """Returns the root-finding problem for the softmax parameter."""

        def optimize_this(beta):
            """Equals zero at the desired value of ``beta``"""
            return np.sum(np.exp(beta * diff) * diff)

        return optimize_this


class Softmax(Policy):
    """Softmax policy.

    Attributes:
        action_space (numpy array of action): Numpy array containing
            all actions available to any agent.
        value_function (fun): A function used by the policy to 
            update values of pi. This is usually a value function 
            learned by a GVF.
        feature_indices (numpy array of bool): Indices of the feature
            vector corresponding to indices used by the
            :py:obj:`value_function`.
    """

    def __init__(self,
                 action_space,
                 value_function,
                 feature_indices,
                 *args, **kwargs):
        kwargs['action_space'] = action_space
        kwargs['value_function'] = value_function
        kwargs['feature_indices'] = feature_indices
        Policy.__init__(self, *args, **kwargs)

    def update(self, phi, *args, **kwargs):
        """Updates :py:attr:`~policy.Policy.pi`."""

        phi = phi[self.feature_indices]

        q_fun = np.vectorize(lambda action: self.value_function(phi, action))
        q_values = q_fun(self.action_space)
        self.pi = tools.softmax(q_values)

class Control(Softmax):
    """Softmax policy.

    Attributes:
        action_space (numpy array of action): Numpy array containing
            all actions available to any agent.
        value_function (fun): A function used by the policy to
            update values of pi. This is usually a value function
            learned by a GVF.
        feature_indices (numpy array of bool): Indices of the feature
            vector corresponding to indices used by the
            :py:obj:`value_function`.
        turn_index (int): Index in :py:attr:`~policy.Policy.action_space`
            of turn action.
        fwd_index (int): Index in :py:attr:`~policy.Policy.action_space`
            of forward action.
    """

    def __init__(self,
                 action_space,
                 value_function,
                 feature_indices,
                 turn_index,
                 fwd_index,
                 *args, **kwargs):
        kwargs['action_space'] = action_space
        kwargs['value_function'] = value_function
        kwargs['feature_indices'] = feature_indices
        Policy.__init__(self, *args, **kwargs)

        self.TURN = turn_index
        self.FORWARD = fwd_index

    def control_cumulant(self, observation):
        """Cumulant to encourage going forward but avoiding bumping.

        Args:
            observation (dictionary): Dictionary containing a ``bump`` key.

        Returns:
            Float representing the cumulant.
        """
        c = 0.0
        if observation is not None and observation['bump']:
            c = -1.0
        elif self.last_index == self.TURN:
            c = 0.0
        elif self.last_index == self.FORWARD:
            c = 0.1
        return c

class GoForward(Policy):
    """Constant policy that only goes forward.

    Attributes:
        action_space (numpy array of action): Numpy array containing
            all actions available to any agent.
        fwd_action_index (int): Index of ``action_space`` containing the
            forward action.
    """

    def __init__(self, action_space, fwd_action_index, *args, **kwargs):
        kwargs['action_space'] = action_space
        Policy.__init__(self, *args, **kwargs)

        self.pi *= 0.0
        self.pi[fwd_action_index] += 1.0

    def update(self, phi, observation, *args, **kwargs):
        pass


class PavlovSoftmax(Policy):
    """Softmax policy that forces the agent to select a "turn"
    action if the bump sensor is on.

    Attributes:
        time_scale (float): Number of seconds in a learning timestep.
        action_space (numpy array of action): Numpy array containing
            all actions available to any agent.
        value_function: A function used by the policy to update values
            of pi. This is usually a value function learned by a GVF.
        feature_indices (numpy array of bool): Indices of the feature
            vector corresponding to indices used by the
            :py:obj:`value_function`.
    """

    def __init__(self,
                 time_scale,
                 action_space,
                 value_function,
                 feature_indices,
                 *args, **kwargs):
        self.TURN = 1
        self.FORWARD = 0

        self.time_scale = time_scale

        kwargs['action_space'] = action_space
        kwargs['value_function'] = value_function
        kwargs['feature_indices'] = feature_indices
        Policy.__init__(self, *args, **kwargs)

    def update(self, phi, observation, *args, **kwargs):
        """Updates :py:attr:`~policy.Policy.pi`."""
        phi = phi[self.feature_indices]
        p = self.value_function(phi)

        if observation['bump']:
            self.pi *= 0
            self.pi[self.TURN] = 1
        else:
            # Joseph Modayil's constants
            # T = 1 / self.time_scale / 1
            T = 6.0
            k1 = np.log((T - 1) * (self.action_space.size - 1))
            k2 = k1 * 4.0

            # make preferences for each action
            prefs = np.zeros(2)

            def last(index):
                return self.last_index == index
            prefs[self.TURN] = k1 * last(self.TURN)
            prefs[self.FORWARD] = k2 * (0.5 - p) + k1 * last(self.FORWARD)

            self.pi = tools.softmax(prefs)


class ForwardIfClear(Policy):
    """Policy that goes forward unless the prediction is high or bumping.

    Args:
        action_space (numpy array of action): Numpy array containing
            all actions available to any agent. The forward action must
            be at the 0th index and the turn action must be at the 1st
            index.
        value_function (fun): A function used by the policy to update
            values of pi. This is usually a value function learned by a
            GVF.
        feature_indices (numpy array of bool): Indices of the feature
            vector corresponding to indices used by the
            :py:obj:`value_function`.
    """

    def __init__(self,
                 action_space,
                 value_function,
                 feature_indices,
                 *args, **kwargs):
        kwargs['action_space'] = action_space
        kwargs['value_function'] = value_function
        kwargs['feature_indices'] = feature_indices
        Policy.__init__(self, *args, **kwargs)

        self.TURN = 1
        self.FORWARD = 0

    def update(self, phi, observation, *args, **kwargs):
        """Updates :py:attr:`~policy.Policy.pi` based on phi."""
        phi = phi[self.feature_indices]

        if self.value_function(phi) > 0.5 or observation['bump']:
            self.last_index = self.TURN
        else:
            self.last_index = self.FORWARD

        self.pi *= 0
        self.pi[self.last_index] = 1


class DeterministicForwardIfClear(Policy):
    """Policy that goes forward unless bumping.

    Args:
        action_space (numpy array of action): Numpy array containing
            all actions available to any agent.
    """

    def __init__(self, *args, **kwargs):
        # where the last action is recorded according
        # to its respective constants
        self.TURN = 1
        self.FORWARD = 0
        # self.STOP = 0

        Policy.__init__(self, *args, **kwargs)

    def update(self, phi, observation, *args, **kwargs):
        phi = phi[self.feature_indices]

        if observation['bump']:
            self.last_index = self.TURN
        else:
            # if self.last_index == self.TURN:
            #     self.last_index = self.STOP
            # else:
            self.last_index = self.FORWARD

        self.pi = np.zeros(self.action_space.size)
        self.pi[self.last_index] = 1


class Switch:
    """Class that switches between two policies.

    Args:
        num_timesteps_explore (int): How long to explore.
        explorer (Policy): Policy to follow while exploring.
        exploiter (Policy): Policy to follow after exploring.
    """

    def __init__(self, explorer, exploiter, num_timesteps_explore):
        self.explorer = explorer
        self.exploiter = exploiter
        self.num_timesteps_explore = num_timesteps_explore
        self.action_space = self.explorer.action_space
        self.last_index = 0
        self.t = 0

    def update(self, *args, **kwargs):
        """Updates the relevant policy according to the timestep."""
        self.t += 1
        if self.t > self.num_timesteps_explore:
            self.exploiter.update(*args, **kwargs)
        else:
            self.explorer.update(*args, **kwargs)

    def get_probability(self, action, *args, **kwargs):
        if self.t > self.num_timesteps_explore:
            policy = self.exploiter
        else:
            policy = self.explorer

        prob = policy.get_probability(action, *args, **kwargs)
        self.last_index = policy.last_index

        return prob

    def choose_action(self, *args, **kwargs):
        if self.t > self.num_timesteps_explore:
            policy = self.exploiter
            msg = "Calling exploitative policy."
        else:
            policy = self.explorer
            msg = "Calling exploratory policy."

        action = policy.choose_action(*args, **kwargs)
        self.last_index = policy.last_index

        rospy.loginfo(msg)
        return action

if __name__ == "__main__":
    try:
        random.seed(20170823)

        # turns on and off the hyperparameter search
        hyperparameter_experiment_mode = False

        action_manager_process = mp.Process(target=start_action_manager,
                                            name="action_manager",
                                            args=())
        action_manager_process.start()

        # robotic parameters
        time_scale = 0.1
        forward_speed = 0.12
        turn_speed = 5. / 3

        time_scale = 0.06
        forward_speed = 0.2
        turn_speed = 25. / 9

        time_scale = 0.03
        forward_speed = 0.4
        turn_speed = 50. / 9

        # all available actions
        action_space = np.array([Twist(Vector3(forward_speed, 0, 0),
                                       Vector3(0, 0, 0)),
                                 Twist(Vector3(0, 0, 0),
                                       Vector3(0, 0, turn_speed))])

        # either cycles through hyperparameter possibilities or 
        # runs wall demo once with default hyperparameters
        if hyperparameter_experiment_mode:
            hyperparameters = [{"alpha0": 0.05, "lmbda": 0.9, 'discount': 0.9,
                                'beta0': 0.05 / 1000
                                },
                               {"alpha0": 0.1, "lmbda": 0.9, 'discount': 0.97,
                                'beta0': 0.1 / 1000
                                }]
            result_file = open('results.txt', 'ab+')
            result_file.write('RESULTS\n')
            result_file.close()
        else:
            hyperparameters = [{'alpha0': 0.05,
                                "lmbda": 0.9,
                                'discount': 0.97,
                                'beta0': 0.05 / 1000,
                                }]

        for hps in hyperparameters:
            # learning parameters
            alpha0 = hps['alpha0']
            lmbda = hps['lmbda']
            discount = hps['discount']

            features_to_use = ['cimage', 'bias']

            feature_indices = np.concatenate(
                    [StateConstants.indices_in_phi[f] for f in
                     features_to_use])
            num_active_features = sum(
                    StateConstants.num_active_features[f] for f in
                    features_to_use)
            num_features = feature_indices.size

            turn_sec_to_bump = 2
            # discount = math.pow(0.75, time_scale / turn_sec_to_bump)
            # discount = 1 - time_scale
            discount_if_bump = lambda obs: 0 if obs["bump"] else discount
            one_if_bump = lambda obs: int(obs['bump']) if obs is not None else 0
            dtb_hp = {'alpha': alpha0 / num_active_features,
                      'beta': hps['beta0'] / num_active_features,
                      'lmbda': lmbda,
                      'alpha0': alpha0,
                      'num_features': num_features,
                      'feature_indices': feature_indices,
                      'eta': 0.001 / num_active_features,
                      'u': 1 / (alpha0 / num_active_features),
                      # 'decay': True,
                      }

            # avoid_wall_omega = 10
            # alpha0 = 0.01
            avoid_wall_hp = {
                        'alpha': alpha0 / num_active_features,
                        'beta': hps['beta0'] / num_active_features,
                        'lmbda': lmbda,
                        'alpha0': alpha0,
                        'num_features': num_features * action_space.size,
                        'feature_indices': feature_indices,
                        }

            # prediction GVF
            dtb_policy = GoForward(action_space=action_space,
                                   fwd_action_index=0)
            dtb_learner = GTD(**dtb_hp)
            # dtb_learner = TOGTD(**dtb_hp)
            # dtb_learner = WISGTD(**dtb_hp)
            # dtb_learner = WISTOGTD(**dtb_hp)

            threshold_policy = PavlovSoftmax(action_space=action_space,
                                             feature_indices=dtb_hp[
                                                 'feature_indices'],
                                             value_function=dtb_learner.predict,
                                             time_scale=time_scale)
            distance_to_bump = GVF(cumulant=one_if_bump,
                                   gamma=discount_if_bump,
                                   target_policy=dtb_policy,
                                   learner=dtb_learner,
                                   name='DistanceToBump',
                                   logger=rospy.loginfo,
                                   **dtb_hp)

            # softmax control GVF
            avoid_wall_learner = GreedyGQ(action_space,
                                          finished_episode=lambda x: False,
                                          **avoid_wall_hp)
            avoid_wall_policy = Control(#omega=avoid_wall_omega,
                            value_function=avoid_wall_learner.predict,
                            action_space=action_space,
                            feature_indices=avoid_wall_hp['feature_indices'],
                            fwd_index=0,
                            turn_index=1)
            avoid_wall = GVF(cumulant=avoid_wall_policy.control_cumulant,
                             gamma=discount_if_bump,
                             target_policy= avoid_wall_policy,
                             learner= avoid_wall_learner,
                             name='AvoidWall',
                             logger=rospy.loginfo,
                             **avoid_wall_hp)

            # # behavior_policy
            # behavior_policy = Switch(explorer=threshold_policy,
            #                          exploiter=avoid_wall_policy,
            #                          num_timesteps_explore=60/time_scale)

            # start processes
            cumulant_counter = Value('d', 0)
            foreground_process = mp.Process(target=start_learning_foreground,
                                            name="foreground",
                                            args=(time_scale,
                                                  [distance_to_bump],
                                                  features_to_use,
                                                  threshold_policy,
                                                  None,
                                                  cumulant_counter))

            foreground_process.start()
            if hyperparameter_experiment_mode is False:
                break
            else:
                # start and stop wall demo if hyper parameter search is on
                rospy.sleep(180)
                foreground_process.terminate()

                # record results of experiment
                result_file = open('results.txt', 'ab+')
                result_file.write(
                    "CUMULANTS: " + str(cumulant_counter.value) + "\n")
                result_file.close()

                # wait for user to continue/reset experiment
                raw_input("Press enter to continue: ")
        if hyperparameter_experiment_mode is True:
            action_manager_process.terminate()

    except rospy.ROSInterruptException as detail:
        rospy.loginfo("Handling: {}".format(detail))
    finally:
        try:
            foreground_process.join()
            action_manager_process.join()
        except NameError:
            pass
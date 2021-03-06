#!/usr/bin/env python
"""Module containing the GreedyGQ algorithm.

Also supports prioritized and uniform experience replay

Authors: 
    Shibhansh Dohare, Niko Yasui.
"""

from __future__ import division

import pickle
import random

import numpy as np
import rospy

import tools


class GreedyGQ:
    """An implementation of GreedyGQ learning algoritm.

    Implementation on greedy_gq based on 
    https://era.library.ualberta.ca/files/8s45q967t/Hamid_Maei_PhDThesis.pdf
    and prioritized experience replay based on 
    https://arxiv.org/pdf/1511.05952.pdf
    Doesn't update some paramenters when we are replaying experience 
    (either uniform or prioritized). 

    Attributes:
        action_space (numpy array of action): Numpy array containing
            all actions available to any agent.
        finished_episode (fun): Function that evaluates if an episode
            has been finished or not.
        num_features (int): The number of features in the state-action
            representation.
        alpha (float): Primary learning rate.
        beta (float): Secondary learning rate
        lmbda (float): Trace decay rate.

        Note: A copy of phi is created during the construction process.

    """

    def __init__(self,
                 action_space,
                 finished_episode,
                 num_features,
                 alpha,
                 beta,
                 lmbda,
                 decay=False,
                 **kwargs):

        self.lmbda = lmbda
        self.alpha = tools.decay(alpha) if decay else tools.constant(alpha)
        self.beta = tools.decay(beta) if decay else tools.constant(beta)
        self.num_features = num_features
        self.action_space = action_space
        self.finished_episode = finished_episode

        # learning 
        self.theta = np.zeros(num_features)
        self.sec_weights = np.zeros(num_features)
        self.e = np.zeros(num_features)
        self.last_gamma = 0
        self.action_phi = np.zeros(num_features)

        # measuring performance
        self.timeStep = 0
        self.average_td_error = 0
        self.average_td_errors = [0]
        self.delta = 0
        self.num_episodes = 0
        self.tderr_elig = np.zeros(num_features)

        # prioritized experience replay
        # list is maintained in the reverse order of td_error
        self.worst_experiences = []
        self.num_experiences = 0

        # helper
        self.get_state_action = tools.action_state_rep(action_space)
        self.episode_finished_last_step = False

    def predict(self, phi, action):
        """Builds the action state reperesentaiton and multiplies by theta.

        Args:
            phi (numpy array of bool): Boolean feature vector.
            action (action): Action that was taken.
        """
        if action is not None:
            q = np.dot(self.get_state_action(phi, action), self.theta)
        else:
            # get average value of actions
            def get_a_phi(actn):
                return self.get_state_action(phi, actn)

            dot_fun = np.vectorize(lambda a: np.dot(get_a_phi(a), self.theta))
            q = np.mean(dot_fun(self.action_space))

        return q

    def update(self,
               phi,
               last_action,
               phi_prime,
               cumulant,
               gamma,
               rho,
               replaying_experience=False,
               **kwargs):
        """Updates the parameters (weights) of the greedy_gq learner.

        Doesn't update some paramenters when we are replaying experience
        (either uniform or prioritized). 

        Parameters:
            phi (numpy array of bool): State at time t.
            last_action (action): Action at time t.
            phi_prime (numpy array of bool): State at time t+1.
            cumulant (float): Cumulant at time t.
            gamma (float): Discounting factor at time t+1.
            rho (float): Off policy importance sampling ratio at time t.
            replaying_experience (bool): True if replaying an 
                experience, false if gathering a new experience from the
                environment.

        Returns:
            self.action_phi (numpy array of bool): Representation for the
                state-action pair at time t. Only used to calculate
                RUPEE.

        """
        if replaying_experience is False:
            # To make sure we don't update anything between the last 
            # termination state and the new start state
            # i.e. Skip one learning step
            if self.episode_finished_last_step:
                self.episode_finished_last_step = False
                return self.action_phi

            new_experience = {'phi': phi,
                              'last_action': last_action,
                              'phi_prime': phi_prime,
                              'cumulant': cumulant,
                              'gamma': gamma,
                              'rho': rho,
                              'id': self.num_experiences
                              }
            self.num_experiences += 1

        self.action_phi = self.get_state_action(phi, last_action)

        action_phi_primes = {
            temp_action: self.get_state_action(phi_prime, temp_action) for
            temp_action in self.action_space}

        action_phis = {
            temp_action: self.get_state_action(phi, temp_action) for
            temp_action in self.action_space}

        # A_{t+1} update
        next_greedy_action = last_action
        for temp_action in self.action_space:
            if np.dot(self.theta,
                      action_phi_primes[temp_action]) >= np.dot(
                    self.theta, action_phi_primes[next_greedy_action]):
                next_greedy_action = temp_action

        # action_phi_bar update
        action_phi_bar = action_phi_primes[next_greedy_action]

        # delta_t update
        self.delta = cumulant + gamma * np.dot(self.theta,
                                               action_phi_bar) - np.dot(
                self.theta, self.action_phi)

        previous_greedy_action = last_action
        for temp_action in self.action_space:
            if np.dot(self.theta, action_phis[temp_action]) >= np.dot(
                    self.theta, action_phis[previous_greedy_action]):
                previous_greedy_action = temp_action

        if np.count_nonzero(self.theta) == 0:
            rospy.logwarn('self.theta in greedy_gq is zero')

        if np.count_nonzero(self.action_phi) == 0:
            rospy.logwarn('self.action_phi in greedy_gq is zero')

        # e_t update
        self.e *= self.last_gamma * self.lmbda * rho
        self.e += self.action_phi  # (phi_t)

        if np.count_nonzero(self.e) == 0:
            rospy.logwarn('self.e in greedy_gq is zero')

        # theta_t update
        self.theta += self.alpha.next() * (self.delta * self.e - self.last_gamma *
                                    (1 - self.lmbda) *
                                    np.dot(self.sec_weights, self.action_phi) *
                                    action_phi_bar)

        # w_t update
        self.sec_weights += self.beta.next() * \
                            (self.delta * self.e - np.dot(self.sec_weights,
                                                          self.action_phi) *
                             self.action_phi)

        # for calculating RUPEE
        self.tderr_elig = self.delta * self.e

        # save gamma
        self.last_gamma = gamma

        if replaying_experience is False:
            new_experience['td_error'] = abs(self.delta)
            self.worst_experiences.append(new_experience)

            # saving the average abs(td_error) of last 1000 time steps
            self.timeStep = self.timeStep + 1
            if len(self.average_td_errors) >= 1000:
                self.average_td_error += (abs(self.delta) - abs(
                        self.average_td_errors[-1000])) / 1000
            else:
                self.average_td_error += (abs(self.delta) - abs(
                        self.average_td_error)) / self.timeStep
            self.average_td_errors.append(self.average_td_error)

            if self.timeStep % 100 == 0:
                with open('average_td_errors', 'w') as f:
                    pickle.dump(self.average_td_errors, f)

            if self.finished_episode(cumulant):
                rospy.loginfo('Episode finished')
                self.episode_finished_last_step = True
                self.num_episodes += 1
                self.e = np.zeros(self.num_features)

        # returing to make sure self.action_phi is used in RUPEE calculation
        return self.action_phi

    def uniform_experience_replay(self, *args, **kwargs):
        """Replays experiences from saved memory.

        Replays ``num_updates_to_make`` experiences. 
        ``self.worst_experiences`` stores the most recent 100
        experiences.

        """
        if self.num_experiences < 1:
            return

        random_indices = []
        num_updates_to_make = 10

        # to avoide memory overflow
        self.worst_experiences = self.worst_experiences[-100:]
        try:
            random_indices = random.sample(
                    range(0, len(self.worst_experiences) - 1),
                    num_updates_to_make)
        except:
            pass

        for experience_index in random_indices:
            temp_experience = self.worst_experiences[experience_index]
            replayed_experience_location_in_sorted_list = experience_index
            self.update(phi=temp_experience['phi'],
                        last_action=temp_experience['last_action'],
                        phi_prime=temp_experience['phi_prime'],
                        cumulant=temp_experience['cumulant'],
                        gamma=temp_experience['gamma'],
                        rho=temp_experience['rho'],
                        replaying_experience=True,
                        experience_id=temp_experience['id'])
            # the new_experience is put back in the array, in the update 
            # function itself
            self.worst_experiences[
                replayed_experience_location_in_sorted_list]['td_error'] = abs(
                    self.delta)

    def td_error_prioritized_experience_replay(self, *args, **kwargs):
        """Replays worst experiences from memory.

        ``self.worst_experiences`` stores the last 100 experiences.
        The ``num_updates_to_make`` experiences with the highest TD
        error are chosen for replay.
        """
        self.worst_experiences = sorted(self.worst_experiences,
                                        key=lambda k: k['td_error'],
                                        reverse=True)[:100]

        if self.num_experiences < 1:
            return
        num_updates_to_make = 10

        for i in range(min(num_updates_to_make, len(self.worst_experiences))):
            # No need to do importance_sampling_correction as we are already
            #  doing off-policy learning
            # pop the next worst experience, prioritized by td_error
            temp_experience = self.worst_experiences[i]
            # print self.worst_experiences[i]['td_error'], 'experience id: 
            # ', temp_experience['id']
            replayed_experience_location_in_sorted_list = i
            self.update(phi=temp_experience['phi'],
                        last_action=temp_experience['last_action'],
                        phi_prime=temp_experience['phi_prime'],
                        cumulant=temp_experience['cumulant'],
                        gamma=temp_experience['gamma'],
                        rho=temp_experience['rho'],
                        replaying_experience=True,
                        experience_id=temp_experience['id'])
            # the new_experience is put back in the heap, in the update
            # function itself
            self.worst_experiences[
                replayed_experience_location_in_sorted_list]['td_error'] = abs(
                    self.delta)

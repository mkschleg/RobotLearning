import multiprocessing as mp
import numpy as np
import random
import rospy
from geometry_msgs.msg import Twist, Vector3

from action_manager import start_action_manager
from gtd import GTD
from egq import GreedyGQ
from gvf import GVF
from learning_foreground import start_learning_foreground
from auto_docking_policies import eGreedy, Learned_Policy
from state_representation import StateConstants

if __name__ == "__main__":
    try:
        # random.seed(20170612)

        time_scale = 0.5
        forward_speed = 0.2
        turn_speed = 2

        alpha0 = 5
        lambda_ = 0.9
        features_to_use = ['imu','ir','bias']
        num_features = np.concatenate([StateConstants.indices_in_phi[f] for f in features_to_use]).size
        alpha = (1 - lambda_) * alpha0 / num_features
        parameters = {'alpha': alpha,
                     'beta': 0.01 * alpha,
                     'lambda': lambda_,
                     'alpha0': alpha0}

        one_if_bump = lambda observation: int(any(observation['bump'])) if observation is not None else 0
        one_if_ir = lambda observation: int(any(observation['ir'])) if observation is not None else 0

        theta = np.zeros(num_features*5)
        phi = np.zeros(num_features)
        observation = None
        learningRate = 0.1/(4*900)
        secondaryLearningRate = learningRate/10
        epsilon = 0.1
        # lambda_ = lambda observation: 0.95
        lambda_ = 0.95
        action_space = [Twist(Vector3(0, 0, 0), Vector3(0, 0, 0)), #stop
                        Twist(Vector3(0.2, 0, 0), Vector3(0, 0, 0)), # forward
                        Twist(Vector3(-0.2, 0, 0), Vector3(0, 0, 0)), # backward
                        Twist(Vector3(0, 0, 0), Vector3(0, 0, 1.5)), # turn acw/cw
                        Twist(Vector3(0, 0, 0), Vector3(0, 0, -1.5)) # turn cw/acw
                        ]
        learned_policy = Learned_Policy(features_to_use=features_to_use,theta=theta,action_space=action_space)

        learner_parameters = {'theta' : theta,
                        'gamma' : 0.9,
                        '_lambda' : lambda_,
                        'cumulant' : one_if_ir,
                        'alpha' : learningRate,
                        'beta' : secondaryLearningRate,
                        'epsilon' : epsilon,
                        'learned_policy': learned_policy,
                        'num_features_state_action': num_features*len(action_space),
                        'features_to_use': features_to_use,
                        'action_space':action_space}

        learner = GreedyGQ(**learner_parameters)
        auto_docking = GVF(num_features=num_features*len(action_space),
                        parameters=parameters,
                        gamma= lambda observation: 0.9,
                        cumulant=one_if_ir,
                        learner=learner,
                        target_policy=learner.learned_policy,
                        name='auto_docking',
                        logger=rospy.loginfo,
                        features_to_use=features_to_use)

        behavior_policy = auto_docking.learner.behavior_policy
        
        foreground_process = mp.Process(target=start_learning_foreground,
                                        name="foreground",
                                        args=(time_scale,
                                              [auto_docking],
                                              features_to_use,
                                              behavior_policy,auto_docking))

        action_manager_process = mp.Process(target=start_action_manager,
                                            name="action_manager",
                                            args=())
        foreground_process.start()
        action_manager_process.start()

    except rospy.ROSInterruptException as detail:
        rospy.loginfo("Handling: {}".format(detail))
    finally:
        try:
            foreground_process.join()
            action_manager_process.join()  
        except NameError:
            pass    
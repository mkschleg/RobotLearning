"""
Author: David Quail, Niko Yasui, June, 2017.

Description:
The GVF base class allows users to instantiate a specific general value function. The GVF could answer any specific question
 by overwriting the behavior policy, cumulant function, or gamma.

 Reference:
 See Adam White's thesis for refernce on GTD, TD, RUPEE and UDE algorithms
 http://homes.soic.indiana.edu/adamw/phd.pdf


Examples:

1. Create a GVF that predicts the value of a certain bit in the feature vector

#For this we create a helper function that will dynamically create cumulant function for us. This way, we could
#create a cumulant function for all 1024 bits in the vector using a for loop

def makeVectorBitCumulantFunction(bitIndex):
    def cumulantFunction(state):
        if (state[bitIndex] == 1):
            return 1
        else:
            return 0
    return cumulantFunction

    gvfs = []

    gvfOn = GVF(TileCoder.numberOfTiles * TileCoder.numberOfTiles * TileCoder.numberOfTilings,
                0.1 / TileCoder.numberOfTilings, isOffPolicy=False, name="NextBitOnPolicy" + str(301))
    gvfOn.cumulant = makeVectorBitCumulantFunction(301)
    gvfs.append(gvfOn)


    vectorSize = TileCoder.numberOfTiles * TileCoder.numberOfTiles * TileCoder.numberOfTilings

    for i in range(0, vectorSize, 1):
        gvfOn = GVF(TileCoder.numberOfTiles*TileCoder.numberOfTiles * TileCoder.numberOfTilings, alpha / TileCoder.numberOfTilings, isOffPolicy = False, name = "NextBitOnPolicy"+ str(i))
        gvfOn.cumulant = makeVectorBitCumulantFunction(i)
        gvfs.append(gvfOn)

        gvfOff = GVF(TileCoder.numberOfTiles * TileCoder.numberOfTiles * TileCoder.numberOfTilings, alpha / TileCoder.numberOfTilings, isOffPolicy=True, name = "NextBitOffPolicy"+ str(i))
        gvfOff.cumulant = makeVectorBitCumulantFunction(i)
        gvfOff.policy = directLeftPolicy
        gvfs.append(gvfOff)


    return gvfs
"""

import numpy as np
import rospy

from tools import equal_twists

class GVF:
    def __init__(self, n_features, alpha, isOffPolicy, learner, name = "GVF name"):

        learners = {"GTD":self._gtdLearn,
                    "TO GTD": self._toGTDlearn,
                    "TD": self._tdLearn}
                    
        self.name = name #Can be useful for debugging, logging, plotting. But not needed otherwise.
        self.isOffPolicy = isOffPolicy #Determines whether td or gtdlearn is used for learning
        self.learn = learners[learner]
        self.lastState = None
        self.lastObservation = 0
        self.weights = np.zeros((1,n_features))
        self.hWeights = np.zeros((1,n_features)) #For GTD off policy learning
        self.hHatWeights = np.zeros((1,n_features)) #For RUPEE error estimation
        self.eligibilityTrace = np.zeros((1,n_features))
        self.elig_delta = np.zeros((1,n_features))
        self.elig_w = np.zeros((1,n_features))
        self.gammaLast = 1
        self.rhoLast = 1
        self.lambdaLast = 0
        self.weightsLast = np.zeros((1,n_features))
        self.weightsL = np.zeros((1,n_features))


        self.alpha = (1.0 - 0.90) * alpha
        self.alphaH = 0.01 * self.alpha #Same alpha for H vector and each HHat vector

        self.alphaRUPEE = 5.0 * self.alpha
        self.betaNotUDE = self.alpha * 10.0
        self.betaNotRUPEE = (1.0 - 0.90) * alpha * 6 / 30
        self.taoRUPEE = 0
        self.taoUDE = 0
        self.movingtdEligErrorAverage = 0 #average of TD*elig*hHat
        self.lastAction = 0

        self.tdVariance = 0
        self.averageTD = 0
        self.i = 1

    """
    GVF Question parameters
    - gamma, cumulant, and policy functions can/should be overiden by the specific instantiation of the GVF based on the
    intended question being asked.
    """
    def gamma(self, state):
        return 0.0

    def cumulant(self, state):
        return 1

    def policy(self, state):
        #To be overwritten based on GVF's intended behavior.
        return None

    """
    Lambda value to control the extent to which updates are backed up
    """
    def lam(self, state):
        return 0.90

    """
    Simple, binary approach to determining whether updates should be 
    applied or not.
    """
    def rho(self, action, state):
        if self.isOffPolicy:
            return equal_twists(action, self.policy(state))
        else:
            return 1

    """
    Prediction and error functions
    """
    def prediction(self, phi):
        return np.inner(self.weights, phi)

    def rupee(self):
        return np.sqrt(np.absolute(np.inner(self.hHatWeights, self.movingtdEligErrorAverage)))

    def ude(self):
        return np.absolute(self.averageTD / (np.sqrt(self.tdVariance) + 0.000001))

    def _toGTDlearn(self, lastState, action, newState):
        if len(lastState) == 0 or len(newState) == 0:
            return None

        rospy.loginfo("!!!!! LEARN  !!!!!!!")
        rospy.loginfo("GVF name: " + str(self.name))

        self.weightsLast = self.weightsL
        self.weightsL = self.weights
        zNext = self.cumulant(newState)
        gammaNext = self.gamma(newState)
        lambdaNext = self.lam(newState)
        rho = self.rho(action, lastState)

        td_err = zNext + gammaNext * np.inner(newState, self.weights) - np.inner(lastState, self.weights)

        self.eligibilityTrace = rho * (self.gammaLast*self.lambdaLast*self.eligibilityTrace + self.alpha*(1-rho*self.gammaLast*self.lambdaLast*np.inner(lastState, self.eligibilityTrace)) * lastState)

        self.elig_delta = rho * (self.gammaLast * self.lambdaLast * self.elig_delta + lastState)

        self.elig_w = self.rhoLast * self.gammaLast * self.lambdaLast * self.elig_w + self.alphaH * (1 - self.rhoLast * self.gammaLast * self.lambdaLast * np.inner(lastState, self.elig_w)) * lastState

        self.weights += td_err * self.eligibilityTrace + (self.eligibilityTrace - self.alpha * rho * newState) * np.inner(self.weights - self.weightsLast, newState) - self.alpha * gammaNext * (1- lambdaNext) * np.inner(self.hWeights, self.elig_delta) * newState

        self.hWeights += rho * td_err * self.elig_w - self.alphaH * np.inner(lastState, self.hWeights) * lastState
 
        self.rhoLast = rho
        self.lambdaLast = lambdaNext
        self.gammaLast = gammaNext





    def _gtdLearn(self, lastState, action, newState):

        if len(lastState) == 0 or len(newState) == 0:
            return None

        rospy.loginfo("!!!!! LEARN  !!!!!!!")
        rospy.loginfo("GVF name: " + str(self.name))

        #rospy.loginfo("action")
        #rospy.loginfo(action)
        zNext = self.cumulant(newState)
        #rospy.loginfo("Cumulant: " + str(zNext))
        gammaNext = self.gamma(newState)
        #rospy.loginfo("gammaNext: " + str(gammaNext))
        lam = self.lam(newState)
        #rospy.loginfo("gammaLast: " + str(self.gammaLast))

        #rospy.loginfo("lambda: " + str(lam))
        rho = self.rho(action, lastState)
        #rospy.loginfo("rho: " + str(rho))
        self.eligibilityTrace = rho * (self.gammaLast * lam * self.eligibilityTrace + lastState)
        tdError = zNext + gammaNext * np.inner(newState, self.weights) - np.inner(lastState, self.weights)


        #rospy.loginfo("tdError: " + str(tdError))

        self.hWeights = self.hWeights + self.alphaH  * (tdError * self.eligibilityTrace - (np.inner(self.hWeights, lastState)) * lastState)

        #update Rupee
        self.hHatWeights = self.hHatWeights + self.alphaRUPEE * (tdError * self.eligibilityTrace - (np.inner(self.hHatWeights, lastState)) * lastState)
        #rospy.loginfo("tao before: " + str(self.tao))
        self.taoRUPEE = (1.0 - self.betaNotRUPEE) * self.taoRUPEE + self.betaNotRUPEE
        #rospy.loginfo("tao after: " + str(self.tao))

        betaRUPEE = self.betaNotRUPEE / self.taoRUPEE
        #rospy.loginfo("beta: " + str(beta))
        self.movingtdEligErrorAverage = (1.0 - betaRUPEE) * self.movingtdEligErrorAverage + betaRUPEE * tdError * self.eligibilityTrace

        #update UDE
        self.taoUDE = (1.0 - self.betaNotUDE) * self.taoUDE + self.betaNotUDE
        betaUDE = self.betaNotUDE / self.taoUDE

        oldAverageTD = self.averageTD
        #rospy.loginfo("Old averageTD:" + str(oldAverageTD))


        self.averageTD = (1.0 - betaUDE) * self.averageTD + betaUDE * tdError
        #rospy.loginfo("New AverageTD: " + str(self.averageTD))
        #rospy.loginfo("tdvariance before: " + str(self.tdVariance))
        self.tdVariance = ((self.i - 1) * self.tdVariance + (tdError - oldAverageTD) * (tdError - self.averageTD)) / self.i
        #rospy.loginfo("td variance after: " + str(self.tdVariance))
        self.i = self.i + 1

        self.weights = self.weights + self.alpha * (tdError * self.eligibilityTrace - gammaNext * (1-lam)  * (np.inner(self.eligibilityTrace, self.hWeights) * newState))

        self.gammaLast = gammaNext


    def _tdLearn(self, lastState, action, newState):
        rospy.loginfo("!!!!! LEARN  !!!!!!!")
        rospy.loginfo("GVF name: " + str(self.name))

        zNext = self.cumulant(newState)
        #rospy.loginfo("Cumulant: " + str(zNext))
        gammaNext = self.gamma(newState)
        #rospy.loginfo("gammaNext: " + str(gammaNext))
        lam = self.lam(newState)
        #rospy.loginfo("gammaLast: " + str(self.gammaLast))

        #rospy.loginfo("lambda: " + str(lam))
        self.eligibilityTrace = self.gammaLast * lam * self.eligibilityTrace + lastState

        tdError = zNext + gammaNext * np.inner(newState, self.weights) - np.inner(lastState, self.weights)

        #rospy.loginfo("tdError: " + str(tdError))

        #update Rupee
        self.hHatWeights = self.hHatWeights + self.alphaRUPEE * (tdError * self.eligibilityTrace - (np.inner(self.hHatWeights, lastState)) * lastState)
        #rospy.loginfo("tao before: " + str(self.tao))
        self.taoRUPEE = (1.0 - self.betaNotRUPEE) * self.taoRUPEE + self.betaNotRUPEE
        #rospy.loginfo("tao after: " + str(self.taoRUPEE))

        betaRUPEE = self.betaNotRUPEE / self.taoRUPEE
        #rospy.loginfo("beta: " + str(beta))
        self.movingtdEligErrorAverage =(1.0 - betaRUPEE) * self.movingtdEligErrorAverage + betaRUPEE * tdError * self.eligibilityTrace


        #update UDE
        self.taoUDE = (1.0 - self.betaNotUDE) * self.taoUDE + self.betaNotUDE
        betaUDE = self.betaNotUDE / self.taoUDE
        oldAverageTD = self.averageTD
        self.averageTD = (1.0 - betaUDE) * self.averageTD + betaUDE * tdError
        self.tdVariance = ((self.i - 1) * self.tdVariance + (tdError - oldAverageTD) * (tdError - self.averageTD)) / self.i
        self.i = self.i + 1

        self.weights = self.weights + self.alpha * tdError * self.eligibilityTrace

        self.gammaLast = gammaNext
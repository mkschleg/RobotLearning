import random
import tiles3
import itertools

from functools import wraps
from time import time

def timing(f):
    @wraps(f)
    def wrap(*args, **kw):
        ts = time()
        result = f(*args, **kw)
        te = time()
        #print 'func:%r args:[%r, %r] took: %2.4f sec' % \
        #  (f.__name__, args, kw, te-ts)
        print 'func:%r took: %2.4f sec' % (f.__name__, te-ts)
        return result
    return wrap


"""
Picks 300 random rgb values from an image and tiles those 
values to obtain the state representation
"""

DIFF_BW_R = 50
DIFF_BW_G = 50
DIFF_BW_B = 50
DIFF_BW_BUMP = 1

NUM_RANDOM_POINTS = 10

class StateRepresentation:
    def __init__(self):
        self.iht = tiles3.IHT(1000000)

    # image: a 2D array with
    @timing
    def RandomPoints(self, image):
        if image == None or len(image) == 0 or len(image[0]) == 0:
            return []

        random_points = []
        
        for p in range(NUM_RANDOM_POINTS):
            p1 = random.randint(0, len(image) - 1)
            p2 = random.randint(0, len(image[0]) - 1)

            random_points.append(image[p1][p2])

        return random_points

    @timing
    def GetStateRepresentation(self, points, action):
        state_representation_raw = []

        rgbpoints_raw = list(itertools.chain.from_iterable(points))
        
        rgb_mod = 0
        for p in range(len(rgbpoints_raw)):
            if (rgb_mod == 0):
                state_representation_raw.append(float(rgbpoints_raw[p]) / DIFF_BW_R)
            elif (rgb_mod == 1):
                state_representation_raw.append(float(rgbpoints_raw[p]) / DIFF_BW_G)
            elif (rgb_mod == 2):
                state_representation_raw.append(float(rgbpoints_raw[p]) / DIFF_BW_B)

            rgb_mod = (rgb_mod + 1) % 3

        # TODO: ADD BUMP SENSOR DATA TO STATE REPRESENTATION
        # TODO: ADD ACTION TO THE STATE REPRESENTATION
        # TODO: PROCESS ACTION APPROPRIATELY
        
        return tiles3.tiles(self.iht, 8, state_representation_raw, [action] ) 

@timing
def DEBUG_generate_rand_image():
    dimensions = [1080, 1080]

    return_matrix =  [[0 for i in xrange(dimensions[1])] for j in xrange(dimensions[0])]

    for i in range(dimensions[0]):
        for j in range(dimensions[1]):
            color = [random.randint(0,255), random.randint(0,255), random.randint(0,255)]
            return_matrix[i][j] = color

    return return_matrix
            
if __name__ == "__main__":
    state_rep = StateRepresentation()

    for i in range(10):
        image = DEBUG_generate_rand_image()
        rand_points = state_rep.RandomPoints(image)
        sr = state_rep.GetStateRepresentation(rand_points, 1)
        print(sr)

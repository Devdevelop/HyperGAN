#From https://gist.github.com/EndingCredits/b5f35e84df10d46cfa716178d9c862a3
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from tensorflow.python.ops import control_flow_ops
from tensorflow.python.ops import math_ops
from tensorflow.python.ops import state_ops
from tensorflow.python.framework import ops
from tensorflow.python.training import optimizer
import tensorflow as tf
import hyperchamber as hc
import numpy as np
import inspect
from operator import itemgetter
from hypergan.train_hooks.base_train_hook import BaseTrainHook

class GpSnMemoryTrainHook(BaseTrainHook):
  def __init__(self, gan=None, config=None, trainer=None, name="GpSnMemoryTrainHook", memory_size=2, top_k=1):
    super().__init__(config=config, gan=gan, trainer=trainer, name=name)
    self.s_max = [ tf.Variable( tf.zeros_like(self.gan.inputs.x)) for i in range(memory_size)]
    self.d_lambda = config['lambda'] or 1

    self.assign_s_max_new_entries = [ tf.assign(self.s_max[i], self.gan.sample_mixture()) for i in range(memory_size) ]
    self.memory_size = memory_size
    self.top_k = top_k

    self.current = tf.Variable(tf.zeros_like(self.gan.inputs.x))
    d = self.gan.create_component(self.gan.config.discriminator, name='discriminator', input=self.current, features=[tf.zeros_like(self.gan.encoder.sample)], reuse=True)
    self.assign_current = [ self.current.assign(self.s_max[i]) for i in range(memory_size) ]
    gd = tf.gradients(d.sample, [self.current])[0]
    self.d_loss = tf.reduce_mean(tf.square(tf.norm(gd, ord=2)))
    self.gan.add_metric('gpsn', self.d_loss)
    if self.config.from_source:
        self.d_loss = tf.reduce_mean(tf.reduce_sum(tf.square(gd), axis=[1]))

  def losses(self):
    return [self.d_lambda * self.d_loss, None]

  def after_step(self, step, feed_dict):
    pass

  def before_step(self, step, feed_dict):
    # get (memory_size - topk) x_hats
    for i,s in enumerate(self.assign_s_max_new_entries[self.top_k:]):
        self.gan.session.run(s)
    # sort memory
    scores = []
    for i in range(self.memory_size):
        self.gan.session.run(self.assign_current[i])
        s = self.gan.session.run(self.d_loss)
        scores.append(s)
    sort = zip(scores, self.s_max, self.assign_s_max_new_entries)
    sort2 = sorted(sort, key=itemgetter(0), reverse=True)
    new_s_max = [s_max for _, s_max,_ in sort2]
    new_assign = [a for _, _,a in sort2]
    self.s_max = new_s_max
    self.assign_s_max_new_entries = new_assign
    # get max
    winner = scores[np.argmax(scores)]

    # truncate memory to top_k
    feed_dict[self.d_loss] = winner

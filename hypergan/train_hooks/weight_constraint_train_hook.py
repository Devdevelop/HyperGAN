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
import inspect
import numpy as np
from hypergan.train_hooks.base_train_hook import BaseTrainHook

class WeightConstraintTrainHook(BaseTrainHook):
  def __init__(self, gan=None, config=None, trainer=None, name="WeightConstraintTrainHook"):
    super().__init__(config=config, gan=gan, trainer=trainer, name=name)
    allvars = gan.variables()
    self.update_weight_constraints = [self._update_weight_constraint(v,i) for i,v in enumerate(allvars)]
    self.update_weight_constraints = [v for v in self.update_weight_constraints if v is not None]
  def _update_ortho(self,v,i):
    s = self.gan.ops.shape(v)
    if len(s) == 4 and s[0] == s[1]:
      w=v
      newv = []
      #s = self.ops.shape(v_transpose)
      #identity = tf.reshape(identity, [s[0],s[1],1,1])
      #identity = tf.tile(identity, [1,1,s[2],s[3]])
      decay = self.config.decay or 0.01
      w = tf.transpose(w, perm=[2,3,0,1])
      for i in range(self.config.iterations or 3):
          wt = tf.transpose(w, perm=[1,0,2,3])
          w2 = tf.reshape(w,[-1, s[0],s[1]])
          wt2 = tf.reshape(wt,[-1, s[0],s[1]])
          wtw = tf.matmul(wt2,w2)
          eye = tf.eye(s[0],s[1])
          eye = tf.tile(eye, [1,s[2]*s[3]])
          eye = tf.reshape(eye, self.gan.ops.shape(w))
          wtw = tf.reshape(wtw, self.gan.ops.shape(w))
          qk = eye - wtw
          w = w * (eye + 0.5*qk)
      w = tf.transpose(w, perm=[2,3,0,1])
      newv = w
      newv=(1.0+decay)*v - decay*(newv)
      newv = tf.reshape(newv,self.ops.shape(v))
      return tf.assign(v, newv)
    else:
      return None


  def _update_ortho2(self,v,i):
    if len(v.shape) == 4:
      w=v
      w = tf.transpose(w, perm=[2,3,0,1])
      identity = tf.cast(tf.diag(np.ones(self.ops.shape(w)[0])), tf.float32)
      wt = tf.transpose(w, perm=[1,0,2,3])
      #s = self.ops.shape(v_transpose)
      #identity = tf.reshape(identity, [s[0],s[1],1,1])
      #identity = tf.tile(identity, [1,1,s[2],s[3]])
      decay = self.config.decay or 0.01
      newv = tf.matmul(w, tf.matmul(wt,w))
      newv = tf.reshape(newv,self.ops.shape(v))
      newv = tf.transpose(newv, perm=[2,3,0,1])
      newv=(1+decay)*v - decay*(newv)

      return tf.assign(v, newv)
    return None
  def _update_lipschitz(self,v,i):
    config = self.config
    if len(v.shape) > 1:
      k = self.config.weight_constraint_k or 100.0000
      wi_hat = v
      if len(v.shape) == 4:
        #fij = tf.reduce_sum(tf.abs(wi_hat),  axis=[0,1])
        fij = wi_hat
        fij = tf.reduce_sum(tf.abs(fij),  axis=[1])
        fij = tf.reduce_max(fij,  axis=[0])
      else:
        fij = wi_hat

      if self.config.ortho_pnorm == "inf":
        wp = tf.reduce_max(tf.reduce_sum(tf.abs(fij), axis=0), axis=0)
      else:
        # conv
        wp = tf.reduce_max(tf.reduce_sum(tf.abs(fij), axis=1), axis=0)
      ratio = (1.0/tf.maximum(1.0, wp/k))
      
      if self.config.weight_bounce:
        bounce = tf.minimum(1.0, tf.ceil(wp/k-0.999))
        ratio -= tf.maximum(0.0, bounce) * 0.2

      if self.config.weight_scaleup:
        up = tf.minimum(1.0, tf.ceil(0.02-wp/k))
        ratio += tf.maximum(0.0, up) * k/wp * 0.2

      wi = ratio*(wi_hat)
      #self.gan.metrics['wi'+str(i)]=wp
      #self.gan.metrics['wk'+str(i)]=ratio
      #self.gan.metrics['bouce'+str(i)]=bounce
      return tf.assign(v, wi)
    return None

  def _update_l2nn(self,v,i):
    config = self.config
    if len(v.shape) == 4:
      w=v
      s = self.gan.ops.shape(v)
      wt = tf.transpose(w, perm=[1,0,2,3])
      w2 = tf.reshape(w,[-1, s[0],s[1]])
      wt2 = tf.reshape(wt,[-1, s[0],s[1]])
      wtw = tf.matmul(wt2,w2)
      wwt = tf.matmul(w2,wt2)
      wtw = tf.reshape(wtw, [-1, self.ops.shape(v)[-1]])
      wwt = tf.reshape(wwt, [-1, self.ops.shape(v)[-1]])
      #wt = tf.transpose(v, perm=[0,1,3,2])
      #wt = tf.transpose(w, perm=[1,0,2,3])
      def _r(m):
        s = self.ops.shape(m)
        m = tf.abs(m)
        m = tf.reduce_sum(m, axis=0,keep_dims=True)
        m = tf.reduce_max(m, axis=1,keep_dims=True)
        #m = tf.tile(m,[s[0],s[1],1,1])
        return m
      bw = tf.minimum(_r(wtw), _r(wwt))
      decay = self.config.decay
      wi = (v/tf.sqrt(bw))
      if decay is not None:
        wi = (1-decay)*v+(decay*wi)
      return tf.assign(v, wi)
    return None

  def _update_weight_constraint(self,v,i):
    config = self.config
    #skipped = [gan.generator.ops.weights[0], gan.generator.ops.weights[-1], gan.discriminator.ops.weights[0], gan.discriminator.ops.weights[-1]]
    #skipped = [gan.discriminator.ops.weights[-1]]
    skipped=[]
    for skip in skipped:
      if self.ops.shape(v) == self.ops.shape(skip):
        print("Skipping constraints on", v)
        return None
    constraints = self.config.weight_constraint or []
    result = []
    if "ortho" in constraints:
      result.append(self._update_ortho(v,i))
    if "ortho2" in constraints:
      result.append(self._update_ortho2(v,i))
    if "lipschitz" in constraints:
      result.append(self._update_lipschitz(v,i))
    if "l2nn" in constraints:
      result.append(self._update_l2nn(v,i))
    if "l2nn-d" in constraints:
      if v in d_vars:
        result.append(self._update_l2nn(v,i))
    result = [r for r in result if r is not None]
    if(len(result) == 0):
      return None
    return tf.group(result)

  def before_step(self, step, feed_dict):
    if self.config.order == "after":
        pass
    else:
        if ((step % (self.config.constraint_every or 100)) == 0):
            #print("Applying weight constraint (pre)")
            self.gan.session.run(self.update_weight_constraints, feed_dict)

  def after_step(self, step, feed_dict):
    if self.config.order == "after":
        if ((step % (self.config.constraint_every or 100)) == 0):
            #print("Applying weight constraint (post)")
            self.gan.session.run(self.update_weight_constraints, feed_dict)

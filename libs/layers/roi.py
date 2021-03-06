from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
import tensorflow as tf

import libs.boxes.cython_bbox as cython_bbox
import libs.configs.config_v1 as cfg
from libs.boxes.bbox_transform import bbox_transform, bbox_transform_inv, clip_boxes
# FLAGS = tf.app.flags.FLAGS

def encode(gt_boxes, rois, num_classes):
  """Matching and Encoding groundtruth boxes (gt_boxes) into learning targets to boxes
  Sampling
  Parameters
  ---------
  gt_boxes an array of shape (G x 5), [x1, y1, x2, y2, class]
  gt_classes an array of shape (G x 1), each value is in [0, num_classes]
  rois an array of shape (R x 4), [x1, y1, x2, y2]
  
  Returns
  --------
  labels: Nx1 array in [0, num_classes)
  rois:   Sampled rois of shape (N, 4)
  bbox_targets: N x (Kx4) regression targets
  bbox_inside_weights: N x (Kx4), in {0, 1} indicating which class is assigned.
  """
  
  all_rois = rois
  num_rois = rois.shape[0]
  # R x G matrix
  overlaps = cython_bbox.bbox_overlaps(
    np.ascontiguousarray(all_rois[:, 0:4], dtype=np.float),
    np.ascontiguousarray(gt_boxes[:, :4], dtype=np.float))
  gt_assignment = overlaps.argmax(axis=1)  # R
  # max_overlaps = overlaps.max(axis=1)      # R
  max_overlaps = overlaps[np.arange(rois.shape[0]), gt_assignment]
  labels = gt_boxes[gt_assignment, 4]

  # sample rois as to 1:3
  fg_inds = np.where(max_overlaps >= cfg.FLAGS.fg_threshold)[0]
  fg_rois = int(min(fg_inds.size, cfg.FLAGS.rois_per_image * cfg.FLAGS.fg_roi_fraction))
  if fg_inds.size > 0:
    fg_inds = np.random.choice(fg_inds, size=fg_rois, replace=False)
  # print(fg_rois)
  
  bg_rois = cfg.FLAGS.rois_per_image - fg_rois
  bg_inds = np.where((max_overlaps < cfg.FLAGS.bg_threshold))[0]
  labels[bg_inds] = 0
  # print(bg_rois)
  if bg_inds.size > 0 and bg_rois < bg_inds.size:
    bg_inds = np.random.choice(bg_inds, size=bg_rois, replace=False)

  keep_inds = np.append(fg_inds, bg_inds)

  bbox_targets, bbox_inside_weights = _compute_targets(
    rois[keep_inds, 0:4], gt_boxes[gt_assignment[keep_inds], :4], labels, num_classes)
  bbox_targets = _unmap(bbox_targets, num_rois, keep_inds, 0)
  bbox_inside_weights = _unmap(bbox_inside_weights, num_rois, keep_inds, 0)
   
  return labels, rois, bbox_targets, bbox_inside_weights

def decode(boxes, scores, rois, ih, iw):
  """Decode prediction targets into boxes and only keep only one boxes of greatest possibility for each rois
    Parameters
  ---------
  boxes: an array of shape (R, Kx4), [x1, y1, x2, y2, x1, x2, y1, y2]
  scores: an array of shape (R, K),
  rois: an array of shape (R, 4), [x1, y1, x2, y2]
  
  Returns
  --------
  final_boxes: of shape (R x 4)
  classes: of shape (R) in {0,1,2,3... K-1}
  scores: of shape (R) in [0 ~ 1]
  """
  boxes = bbox_transform_inv(rois, deltas=boxes)
  classes = np.argmax(scores, axis=1)
  scores = np.max(scores, axis=1)
  final_boxes = np.zeros((boxes.shape[0], 4))
  for i in np.arange(0, boxes.shape[0]):
    ind = classes[i]*4
    final_boxes[i, 0:4] = boxes[i, ind:ind+4]
  final_boxes = clip_boxes(final_boxes, (ih, iw))
  return final_boxes, classes, scores

def _compute_targets(ex_rois, gt_rois, labels, num_classes):
  """
  This function expands those targets into the 4-of-4*K representation used
  by the network (i.e. only one class has non-zero targets).
  
  Returns:
    bbox_target (ndarray): N x 4K blob of regression targets
    bbox_inside_weights (ndarray): N x 4K blob of loss weights
  """

  assert ex_rois.shape[0] == gt_rois.shape[0]
  assert ex_rois.shape[1] == 4
  assert gt_rois.shape[1] == 4

  targets = bbox_transform(ex_rois, gt_rois)

  clss = labels
  bbox_targets = np.zeros((clss.size, 4 * num_classes), dtype=np.float32)
  bbox_inside_weights = np.zeros(bbox_targets.shape, dtype=np.float32)
  inds = np.where(clss > 0)[0]
  for ind in inds:
    cls = int(clss[ind])
    start = 4 * cls
    end = start + 4
    bbox_targets[ind, start:end] = targets[ind, 0:4]
    bbox_inside_weights[ind, start:end] = 1
  return bbox_targets, bbox_inside_weights

def _unmap(data, count, inds, fill=0):
  """ Unmap a subset of item (data) back to the original set of items (of
  size count) """
  if len(data.shape) == 1:
    ret = np.empty((count,), dtype=np.float32)
    ret.fill(fill)
    ret[inds] = data
  else:
    ret = np.empty((count,) + data.shape[1:], dtype=np.float32)
    ret.fill(fill)
    ret[inds, :] = data
  return ret

if __name__ == '__main__':
  cfg.FLAGS.fg_threshold = 0.1
  classes = np.random.randint(0, 3, (10, 1))
  boxes = np.random.randint(10, 50, (10, 2))
  s = np.random.randint(10, 20, (10, 2))
  s = boxes + s
  boxes = np.concatenate((boxes, s), axis=1)
  gt_boxes = np.hstack((boxes, classes))
  noise = np.random.randint(-3, 3, (10, 4))
  rois = gt_boxes[:, :4] + noise
  labels, rois, bbox_targets, bbox_inside_weights = encode(gt_boxes, rois, num_classes=3)
  print (labels)
  print (bbox_inside_weights)
  
  ls = np.zeros((labels.shape[0], 3))
  for i in range(labels.shape[0]):
    ls[i, labels[i]] = 1
  final_boxes, classes, scores = decode(bbox_targets, ls, rois, 100, 100)
  print('gt_boxes:\n', gt_boxes)
  print ('final boxes:\n', np.hstack((final_boxes, np.expand_dims(classes, axis=1))).astype(np.int32))
  # print (final_boxes.astype(np.int32))
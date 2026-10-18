import time
import cv2
import numpy as np
from rknn_native import RKNN

from configuration import settings
MODEL = str(settings.model)

class PersonDetector:
    def __init__(self):
        if settings.model_manifest:
            from model_contract import validate_manifest
            validate_manifest(MODEL,settings.model_manifest)
        self.runtime = RKNN(MODEL)
        self.profile={'preprocess_seconds':0.,'inference_seconds':0.,'postprocess_seconds':0.}

    def detect(self, rgb, threshold=0.25):
        preparing=time.monotonic()
        h, w = rgb.shape[:2]
        ratio = min(640 / w, 640 / h)
        nw, nh = round(w * ratio), round(h * ratio)
        left, top = (640 - nw) // 2, (640 - nh) // 2
        image = np.zeros((640, 640, 3), np.uint8)
        image[top:top+nh, left:left+nw] = cv2.resize(rgb, (nw, nh))
        started = time.monotonic()
        self.profile['preprocess_seconds']+=started-preparing
        outputs = self.runtime.infer(image)
        inference = time.monotonic() - started
        self.profile['inference_seconds']+=inference
        postprocessing=time.monotonic()
        boxes, scores = [], []
        for i in (0, 3, 6):
            reg, cls = outputs[i], outputs[i+1]
            _, channels, gh, gw = reg.shape
            # COCO class 0 is person; retain only anchors whose top class is person.
            score = cls[0, 0]
            ys, xs = np.where((score >= threshold) & (np.argmax(cls[0], axis=0) == 0))
            if not len(xs):
                continue
            logits = reg[0, :, ys, xs].reshape(-1, 4, channels // 4)
            logits -= logits.max(axis=2, keepdims=True)
            probs = np.exp(logits)
            probs /= probs.sum(axis=2, keepdims=True)
            distance = (probs * np.arange(channels // 4)).sum(axis=2)
            sx, sy = 640 / gw, 640 / gh
            b = np.stack(((xs+.5-distance[:,0])*sx, (ys+.5-distance[:,1])*sy,
                          (xs+.5+distance[:,2])*sx, (ys+.5+distance[:,3])*sy), axis=1)
            boxes.extend(b.tolist()); scores.extend(score[ys, xs].tolist())
        if not boxes:
            self.profile['postprocess_seconds']+=time.monotonic()-postprocessing
            return [], inference
        boxes, scores = np.array(boxes), np.array(scores)
        order = scores.argsort()[::-1]
        keep = []
        while len(order):
            index = order[0]; keep.append(index)
            other = order[1:]
            if not len(other): break
            lo = np.maximum(boxes[index,:2], boxes[other,:2])
            hi = np.minimum(boxes[index,2:], boxes[other,2:])
            inter = np.maximum(hi-lo, 0).prod(axis=1)
            area = np.maximum(boxes[:,2:]-boxes[:,:2], 0).prod(axis=1)
            iou = inter / np.maximum(area[index]+area[other]-inter, 1e-6)
            order = other[iou <= .45]
        result = []
        for i in keep:
            b = (boxes[i] - np.array([left, top, left, top])) / ratio
            b[::2] = np.clip(b[::2], 0, w)
            b[1::2] = np.clip(b[1::2], 0, h)
            if b[2] <= b[0] or b[3] <= b[1]: continue
            result.append({'score': float(scores[i]), 'box': b.tolist()})
        self.profile['postprocess_seconds']+=time.monotonic()-postprocessing
        return result, inference

    def presence(self,rgb):
        start=time.monotonic();h,w=rgb.shape[:2];ratio=min(640/w,640/h)
        nw,nh=round(w*ratio),round(h*ratio)
        image=np.zeros((640,640,3),np.uint8)
        image[(640-nh)//2:(640-nh)//2+nh,(640-nw)//2:(640-nw)//2+nw]=rgb if (nw,nh)==(w,h) else cv2.resize(rgb,(nw,nh))
        ready=time.monotonic();self.profile['preprocess_seconds']+=ready-start
        score=self.runtime.person_score(image)
        self.profile['inference_seconds']+=time.monotonic()-ready
        return score

    def close(self):
        self.runtime.close()

    def multiview(self, rgb, rotations=(1,2,3)):
        baseline, elapsed = self.detect(rgb, .15)
        result = [dict(p, rotation=0) for p in baseline]
        if any(p['score'] >= .25 for p in baseline):
            return baseline, result, elapsed
        h, w = rgb.shape[:2]
        for k in rotations:
            people, seconds = self.detect(np.rot90(rgb, k).copy(), .15)
            elapsed += seconds
            for p in people:
                x1, y1, x2, y2 = p['box']
                if k == 1: box = [w-y2, x1, w-y1, x2]
                elif k == 2: box = [w-x2, h-y2, w-x1, h-y1]
                else: box = [y1, h-x2, y2, h-x1]
                result.append({'box': box, 'score': p['score'], 'rotation': k*90})
        return baseline, result, elapsed

    def zoom_fallback(self, rgb, threshold=.35):
        """Overlapping vertical tiles enlarge small people; detection only."""
        h,w=rgb.shape[:2]
        result=[];elapsed=0
        for x1,x2 in ((0,round(w*.55)),(round(w*.45),w)):
            people,seconds=self.detect(rgb[:,x1:x2],threshold)
            elapsed+=seconds
            for p in people:
                b=p['box'];b[0]+=x1;b[2]+=x1
                result.append(dict(p,box=b,rotation=0,crop='{}:{}'.format(x1,x2)))
        return result,elapsed

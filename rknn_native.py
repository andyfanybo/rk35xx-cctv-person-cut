"""Minimal RKNN C API wrapper; no changes to system runtime libraries."""
import ctypes as C
import ctypes.util
from configuration import settings
import math
import numpy as np
from pathlib import Path

class Attr(C.Structure):
    _fields_ = [('index', C.c_uint32), ('n_dims', C.c_uint32),
        ('dims', C.c_uint32 * 16), ('name', C.c_char * 256),
        ('n_elems', C.c_uint32), ('size', C.c_uint32), ('fmt', C.c_int),
        ('type', C.c_int), ('qnt_type', C.c_int), ('fl', C.c_int8),
        ('zp', C.c_int32), ('scale', C.c_float), ('w_stride', C.c_uint32),
        ('size_with_stride', C.c_uint32), ('pass_through', C.c_uint8),
        ('h_stride', C.c_uint32)]

class Input(C.Structure):
    _fields_ = [('index', C.c_uint32), ('buf', C.c_void_p), ('size', C.c_uint32),
        ('pass_through', C.c_uint8), ('type', C.c_int), ('fmt', C.c_int)]

class Output(C.Structure):
    _fields_ = [('want_float', C.c_uint8), ('is_prealloc', C.c_uint8),
        ('index', C.c_uint32), ('buf', C.c_void_p), ('size', C.c_uint32)]

class SDKVersion(C.Structure):
    _fields_=[("api_version",C.c_char*256),("drv_version",C.c_char*256)]

class RKNN:
    def __init__(self, model):
        if C.sizeof(C.c_void_p)!=8:raise RuntimeError('This release requires 64-bit Linux userspace')
        library=settings.rknn_library or C.util.find_library('rknnrt')
        if not library:raise RuntimeError('RKNN runtime not found; configure rknn_library or PERSON_RKNN_LIBRARY')
        self.library_path=library
        self.lib=C.CDLL(library)
        signatures = {'rknn_init': [C.POINTER(C.c_uint64), C.c_void_p, C.c_uint32, C.c_uint32, C.c_void_p],
            'rknn_query': [C.c_uint64, C.c_int, C.c_void_p, C.c_uint32],
            'rknn_inputs_set': [C.c_uint64, C.c_uint32, C.POINTER(Input)],
            'rknn_run': [C.c_uint64, C.c_void_p],
            'rknn_outputs_get': [C.c_uint64, C.c_uint32, C.POINTER(Output), C.c_void_p],
            'rknn_outputs_release': [C.c_uint64, C.c_uint32, C.POINTER(Output)],
            'rknn_destroy': [C.c_uint64]}
        for name, args in signatures.items():
            getattr(self.lib, name).argtypes = args
            getattr(self.lib, name).restype = C.c_int
        self.ctx = C.c_uint64()
        data = Path(model).read_bytes()
        if not data:raise RuntimeError("RKNN model is empty")
        buf = C.create_string_buffer(data)
        self.check(self.lib.rknn_init(C.byref(self.ctx), buf, len(data), 2, None))
        try:
            counts = (C.c_uint32 * 2)()
            self.check(self.lib.rknn_query(self.ctx, 0, counts, C.sizeof(counts)))
            self.sdk=SDKVersion()
            self.check(self.lib.rknn_query(self.ctx,5,C.byref(self.sdk),C.sizeof(self.sdk)))
            if counts[0]!=1 or counts[1] not in (9,):
                self.close();raise RuntimeError("Expected one input and nine Rockchip YOLOv8 outputs")
            self.inputs = self.attributes(1, counts[0])
            self.outputs = self.attributes(2, counts[1])
            self.presence_float_compatible=len(self.outputs)>=8 and all(a.n_dims==4 and a.dims[0]==1 and a.dims[1]==80 and a.dims[2]>0 and a.dims[3]>0 and a.fmt==0 for a in [self.outputs[i] for i in (1,4,7)])
            a=self.inputs[0]
            shape=list(a.dims[:a.n_dims])
            if a.n_dims!=4 or shape not in ([1,640,640,3],[1,3,640,640]) or not self.presence_float_compatible:
                self.close();raise RuntimeError('Model input/output contract does not match 640x640 COCO YOLOv8')
            for i in (0,3,6):
                reg,cls,total=self.outputs[i:i+3]
                if reg.n_dims!=4 or reg.fmt!=0 or reg.dims[0]!=1 or reg.dims[1]<4 or reg.dims[1]%4 or list(reg.dims[2:4])!=list(cls.dims[2:4]):
                    raise RuntimeError('Unsupported YOLOv8 regression output layout')
                if total.n_dims!=4 or total.fmt!=0 or list(total.dims[:2])!=[1,1] or list(total.dims[2:4])!=list(cls.dims[2:4]):
                    raise RuntimeError('Unsupported YOLOv8 auxiliary output layout')
            self.presence_native_ok=self.presence_float_compatible and len(self.outputs)==9 and all(a.type in (2,3) and a.qnt_type==2 and math.isfinite(a.scale) and a.scale>0 and a.n_elems==a.dims[1]*a.dims[2]*a.dims[3] and a.size==a.n_elems and a.w_stride in (0,a.dims[3]) for a in [self.outputs[i] for i in (1,4,7)])
        except BaseException:
            self.close()
            raise

    def check(self, result):
        if result != 0:
            raise RuntimeError('RKNN error {}'.format(result))

    def attributes(self, cmd, count):
        result = []
        for i in range(count):
            a = Attr(); a.index = i
            self.check(self.lib.rknn_query(self.ctx, cmd, C.byref(a), C.sizeof(a)))
            result.append(a)
        return result

    def infer(self, image):
        image = np.ascontiguousarray(image)
        inp = Input(0, image.ctypes.data, image.nbytes, 0,
                    0 if image.dtype == np.float32 else 3, 1)
        self.check(self.lib.rknn_inputs_set(self.ctx, 1, C.byref(inp)))
        self.check(self.lib.rknn_run(self.ctx, None))
        out = (Output * len(self.outputs))()
        for i in range(len(out)):
            out[i].index = i; out[i].want_float = 1
        self.check(self.lib.rknn_outputs_get(self.ctx, len(out), out, None))
        try:
            result = []
            for o, a in zip(out, self.outputs):
                if not o.buf or o.size!=a.n_elems*4:
                    raise RuntimeError('Unexpected RKNN float output buffer size')
                values = np.ctypeslib.as_array(C.cast(o.buf, C.POINTER(C.c_float)), shape=(o.size // 4,)).copy()
                result.append(values.reshape(tuple(a.dims[:a.n_dims])))
            return result
        finally:
            self.lib.rknn_outputs_release(self.ctx, len(out), out)

    def person_score(self,image):
        # Validate once at model load; unexpected native strides use float outputs.
        if not self.presence_float_compatible:raise RuntimeError('模型输出不是预期的3尺度COCO人员布局')
        if not self.presence_native_ok:
            values=self.infer(image)
            return max(float(values[i][0,0].max()) for i in (1,4,7))
        image=np.ascontiguousarray(image)
        inp=Input(0,image.ctypes.data,image.nbytes,0,3,1)
        self.check(self.lib.rknn_inputs_set(self.ctx,1,C.byref(inp)))
        self.check(self.lib.rknn_run(self.ctx,None))
        out=(Output*len(self.outputs))()
        for i in range(len(out)):out[i].index=i;out[i].want_float=0
        self.check(self.lib.rknn_outputs_get(self.ctx,len(out),out,None))
        try:
            score=0.
            for i in (1,4,7):
                a=self.outputs[i];o=out[i]
                if o.size!=a.n_elems:
                    self.presence_native_ok=False
                    break
                dtype=C.c_int8 if a.type==2 else C.c_uint8
                plane=int(a.dims[2])*int(a.dims[3])
                values=np.ctypeslib.as_array(C.cast(o.buf,C.POINTER(dtype)),shape=(o.size,))
                score=max(score,(int(values[:plane].max())-a.zp)*a.scale)
            else:return score
        finally:self.lib.rknn_outputs_release(self.ctx,len(out),out)
        return self.person_score(image)

    def close(self):
        if self.ctx.value:
            self.lib.rknn_destroy(self.ctx)
            self.ctx.value = 0

if __name__ == '__main__':
    import sys
    r = RKNN(sys.argv[1])
    for direction, attrs in [('input', r.inputs), ('output', r.outputs)]:
        for a in attrs:
            print(direction, a.index, a.name.decode(), list(a.dims[:a.n_dims]), 'fmt', a.fmt, 'type', a.type, 'scale', a.scale, flush=True)
    r.close()

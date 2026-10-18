"""Read-only MP4 box traversal."""
import struct

def boxes(data, start, end):
    while start < end:
        if start+8 > end: raise ValueError('Truncated MP4 box')
        size = struct.unpack_from('>I',data,start)[0]
        name = bytes(data[start+4:start+8])
        header = 8
        if size == 1:
            size = struct.unpack_from('>Q',data,start+8)[0];header=16
        elif size == 0: size=end-start
        if size<header or start+size>end: raise ValueError('Invalid MP4 box')
        yield name,start+header,start+size
        start+=size

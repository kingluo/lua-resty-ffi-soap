#
# Copyright (c) 2023, Jinhua Luo (kingluo) luajit.io@gmail.com
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
from cffi import FFI

ffi = FFI()
ffi.cdef(
    """
void* malloc(size_t);
void *memcpy(void *dest, const void *src, size_t n);
void* ngx_http_lua_ffi_task_poll(void *p);
char* ngx_http_lua_ffi_get_req(void *tsk, int *len);
void ngx_http_lua_ffi_respond(void *tsk, int rc, char* rsp, int rsp_len);
"""
)
C = ffi.dlopen(None)

from enum import Enum
import asyncio
import json
import jsonpickle
import threading
import traceback
import zeep


class CMD(Enum):
    NEW_CLIENT = 1
    CLOSE_CLIENT = 2
    OPERATION = 3


class State:
    def __init__(self, cfg):
        self.clients = {}
        self.idx = 0
        self.loop = asyncio.new_event_loop()
        t = threading.Thread(target=self.loop.run_forever)
        t.daemon = True
        t.start()
        self.event_loop_thread = t

    async def close_client(self, req, task):
        idx = req["client"]
        client = self.clients[idx]["client"]
        del self.clients[idx]
        await client.transport.aclose()
        C.ngx_http_lua_ffi_respond(task, 0, ffi.NULL, 0)

    async def new_client(self, req, task):
        self.idx += 1
        idx = self.idx

        cfg = req["data"]
        client = zeep.AsyncClient(wsdl=cfg["wsdl_url"])

        self.clients[idx] = {"client": client, "cfg": cfg}
        data = json.dumps({"client": idx})
        res = C.malloc(len(data))
        C.memcpy(res, data.encode(), len(data))
        C.ngx_http_lua_ffi_respond(task, 0, res, len(data))

    async def operation(self, req, task):
        idx = req["client"]
        client = self.clients[idx]

        try:
            cfg = req["data"]
            operation = cfg["operation"]
            body = cfg["body"]
            print(f"req: {body}")
            body = await client["client"].service[operation](**body)
            out = {"response": body}
        except zeep.exceptions.Fault as fault:
            body = fault
            out = {"fault": fault}

        print(f"resp: {jsonpickle.encode(body)}")

        data = json.dumps(
            out,
            default=lambda o: hasattr(o, "__values__") and o.__values__ or o.__dict__,
        )
        res = C.malloc(len(data))
        C.memcpy(res, data.encode(), len(data))
        C.ngx_http_lua_ffi_respond(task, 0, res, len(data))

    async def dispatch(self, req, task):
        try:
            cmd = CMD(req["cmd"]).name.lower()
            return await getattr(self, cmd)(req, task)
        except Exception as exc:
            tb = traceback.format_exc()
            print(tb)
            res = C.malloc(len(tb))
            C.memcpy(res, tb.encode(), len(tb))
            C.ngx_http_lua_ffi_respond(task, 1, res, 0)

    async def close(self, req, task):
        for _, client in self.clients:
            await client.transport.aclose()
        self.loop.stop()

    def poll(self, tq):
        while True:
            task = C.ngx_http_lua_ffi_task_poll(ffi.cast("void*", tq))
            if task == ffi.NULL:
                asyncio.run_coroutine_threadsafe(self.close(req, task), self.loop)
                self.event_loop_thread.join()
                break
            r = C.ngx_http_lua_ffi_get_req(task, ffi.NULL)
            req = json.loads(ffi.string(r))
            asyncio.run_coroutine_threadsafe(self.dispatch(req, task), self.loop)


def init(cfg, tq):
    data = ffi.string(ffi.cast("char*", cfg))
    cfg = json.loads(data)
    st = State(cfg)
    t = threading.Thread(target=st.poll, args=(tq,))
    t.daemon = True
    t.start()
    return 0

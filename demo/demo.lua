local soap = require("resty.ffi.soap")
local cjson = require("cjson")

local _M = {}

local idx = 1
local clients = {}

local function read_body()
    ngx.req.read_body()
    return cjson.decode(ngx.req.get_body_data())
end

function _M.create_client()
    local client, rc, err = soap.new(read_body())
    if err then
        ngx.status = 502
        ngx.say("rc=", require("inspect")(rc))
        ngx.say("err=", require("inspect")(err))
        ngx.exit(ngx.HTTP_OK)
    end
    assert(client)
    clients[idx] = client
    ngx.say(string.format([[{"client": %d}]], idx))
    idx = idx + 1
end

function _M.operation()
    local idx = tonumber(ngx.var.arg_client)
    local client = clients[idx]
    local opts = {
        operation = ngx.var.arg_operation,
        body = read_body(),
    }
    local ok, res, err = client:operation(opts)
    ngx.say(cjson.encode(res))
    if err then
        ngx.say(require("inspect")(err))
    end
end

function _M.close_client()
    local idx = tonumber(ngx.var.arg_client)
    local client = clients[idx]
    local ok, err1, err2 = client:close()
    clients[idx] = nil
    ngx.say("ok")
end

return _M

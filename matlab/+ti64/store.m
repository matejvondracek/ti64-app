function varargout = store(action, varargin)
% STORE  Session object store: MTEX objects stay in MATLAB and Python refers to them by ID.
%   id  = ti64.store('put', obj)      add an object, returns a new char ID
%   obj = ti64.store('get', id)       error 'ti64:store:missing' if unknown
%   ti64.store('remove', id)          drop one object (no error if unknown)
%   ids = ti64.store('list')          cell array of IDs
%   ti64.store('clear')               drop everything

persistent objs
if isempty(objs)
  objs = containers.Map('KeyType', 'char', 'ValueType', 'any');
end
switch action
  case 'put'
    id = erase(char(java.util.UUID.randomUUID()), '-');
    id = id(1:12);
    objs(id) = varargin{1};
    varargout{1} = id;
  case 'get'
    id = char(varargin{1});
    if ~isKey(objs, id)
      error('ti64:store:missing', 'No object with ID %s in the MATLAB session', id);
    end
    varargout{1} = objs(id);
  case 'remove'
    id = char(varargin{1});
    if isKey(objs, id), remove(objs, id); end
  case 'list'
    varargout{1} = keys(objs);
  case 'clear'
    objs = containers.Map('KeyType', 'char', 'ValueType', 'any');
  otherwise
    error('ti64:store:action', 'Unknown action %s', action);
end
end

# Copyright (c) 2013 Jordan Halterman <jordan.halterman@gmail.com>
# See LICENSE for details.
from redis import Redis
from registry import DataType as DataTypeRegistry
from registry import Observable as ObservableRegistry
from exception import *
import uuid

class ActiveRedis(object):
  """
  Active Redis client.
  """
  def __init__(self, *args, **kwargs):
    """Initializes the client.

    The constructor accepts either a Redis instance or arguments
    required to construct a Redis instance.
    """
    if len(args) == 0 and len(kwargs) == 0:
      if len(self.default_config) == 0:
        self.client = Redis()
      else:
        try:
          self.default_config[1]
          self.client = Redis(*self.default_config[0], **self.default_config[1])
        except IndexError:
          if isinstance(self.default_config[0], dict):
            self.client = Redis(**self.default_config[0])
          else:
            self.client = Redis(*self.default_config[0])
    else:
      try:
        if isinstance(args[0], Redis):
          self.client = args[0]
        else:
          self.client = Redis(*args, **kwargs)
      except IndexError:
        self.client = Redis(*args, **kwargs)
    self.encoder = Encoder(self.client)
    self.encode = self.encoder.encode
    self.decode = self.encoder.decode

  @staticmethod
  def _create_unique_key():
    """Generates a unique Redis key using UUID."""
    return uuid.uuid4()

  @staticmethod
  def _wrap_datatype(datatype):
    """Wraps a datatype constructor."""
    def create_datatype(key=None):
      return datatype(key or ActiveRedis._create_unique_key(), self.client)
    return create_datatype

  def __getattr__(self, name):
    if DataType.exists(name):
      return ActiveRedis._wrap_datatype(DataType.get(name))
    else:
      raise AttributeError("Attribute %s not found." % (name,))

class Encoder(object):
  """
  Handles encoding and decoding of objects.
  """
  REDIS_STRUCTURE_PREFIX = 'redis:struct'
  ABSOLUTE_VALUE_PREFIX = 'redis:abs'

  def __init__(self, client):
    self.client = client

  def encode(self, item):
    """Encodes a Python object."""
    if isinstance(item, Wrapper):
      item = item.subject
    if self._is_redis_item(item):
      return self._encode_redis_item(item)
    else:
      return self._encode_structure_item(item)

  def _is_redis_item(self, item):
    """Indicaites whether the item is a Redis data type."""
    return isinstance(item, DataType)

  def _encode_redis_item(self, item):
    """Encodes a Redis data type."""
    return "%s:%s" % (self.REDIS_STRUCTURE_PREFIX, item.key)

  def _encode_structure_item(self, item):
    """Encodes a structure."""
    return "%s:%s" % (self.ABSOLUTE_VALUE_PREFIX, json.dumps(item))

  def decode(self, value):
    """Decodes a stored value."""
    if self._is_redis_value(value):
      return self._decode_redis_value(value)
    elif self._is_structure_value(value):
      return self._decode_structure_value(value)
    else:
      raise EncodingError("Failed to decode value. Unknown data type.")

  def _is_redis_value(self, value):
    """Indicates whether the value is a Redis data type."""
    return value.startswith(self.REDIS_STRUCTURE_PREFIX)

  def _decode_redis_value(self, value):
    """Decodes a Redis data type value."""
    key = value[len(self.REDIS_STRUCTURE_PREFIX)+1:]
    type = self.client.type(key)
    if type is None or type == 'none':
      raise EncodingError("Failed to decode value. Key %s does not exist." % (key,))
    return DataType.get(type)(key, self.client)

  def _is_structure_value(self, value):
    """Indicates whether the value is a Python structure."""
    return value.startswith(self.ABSOLUTE_VALUE_PREFIX)

  def _decode_structure_value(self, value):
    """Decodes a structure value."""
    return json.loads(value[len(self.ABSOLUTE_VALUE_PREFIX)+1:])

class DataType(object):
  """
  Abstract data type class.
  """
  _registry = DataTypeRegistry
  _scripts = {}

  def __init__(self, key, client):
    self.key = key
    self.client = client

  def __new__(cls, name, bases, attr):
    attr['_scripts']['delete_all'] = DeleteAll
    return object.__new__(cls, name, bases, attr)

  @classmethod
  def exists(cls, type):
    """Indicates whether a data type handler exists."""
    return cls._registry.exists(type)

  @classmethod
  def get(cls, type):
    """Returns a data type handler."""
    return cls._registry.get(type)

  def __setattr__(self, name, value):
    """Allows the data type key to be changed."""
    if name == 'key' and hasattr(self, 'key'):
      self.client.rename(self.key, value)
      self.key = value

  def _load_script(self, script):
    """Loads a script handler."""
    try:
      return self._scripts[script](self.key.client)
    except KeyError:
      raise ScriptError("Invalid script %s." % (script,))

  def _execute_script(self, script, *args, **kwargs):
    """Executes a script."""
    return self._load_script(script)(*args, **kwargs)

  def delete(self):
    """Deletes the data type."""
    self._execute_script('delete_all', self.key)

class Observer(object):
  """
  Abstract base class for notifiable data types.

  This class should be extended by data types using multiple
  inheritence. Observables can be created using the self.observe(subject)
  method. This allows standard Python data structures such as lists,
  sets, and any other data type to be observed for changes.

  Observable handlers must be registered in the Active Redis
  registry. See the Observable class for more.
  """
  def observe(self, subject, *args, **kwargs):
    """Creates an observer for the given subject."""
    if Observable.is_observable(subject):
      return Notifier(Observable.get(subject)(subject, *args, **kwargs), self)
    else:
      return subject

  def notify(self, subject, *args, **kwargs):
    """Notifies the data type of a change in an observable."""
    raise NotImplementedError("Notifiable data types must implement the notify() method.")

class Notifier(object):
  """
  Monitors an observable object and notifies the observer when
  an observable method is called.
  """
  def __init__(self, observable, observer):
    self.observable = observable
    self.observer = observer

  def wrap_method(self, name):
    def execute_method(*args, **kwargs):
      retval = getattr(self.observable, name)(*args, **kwargs)
      self.observer.notify(self.observable.subject, *self.observable.args, **self.observable.kwargs)
      return retval
    return execute_method

  def __getattr__(self, name):
    """Checks for a method that needs to be wrapped."""
    if name in self.watch_methods and callable(getattr(self.observable, name)):
      return self.wrap_method(name)
    elif hasattr(self.observable, name):
      return getattr(self.observable, name)
    else:
      raise AttributeError("Attribute %s not found." % (name,))

  def __repr__(self):
    return repr(self.subject)

class Observable(object):
  """
  Wrapper for observable objects.
  """
  _registry = ObservableRegistry

  type = None
  watch_methods = []

  def __init__(self, subject, *args, **kwargs):
    self.subject = subject
    self.args = args
    self.kwargs = kwargs

  def __getattr__(self, name):
    if hasattr(self.subject, name):
      return getattr(self.subject, name)
    else:
      raise AttributeError("Attribute %s not found." % (name,))

  @classmethod
  def is_observable(cls, type):
    """Indicates whether the given type is observable."""
    return cls._registry.is_observable(type)

  @classmethod
  def get_observable(cls, type):
    """Returns an observable handler for a data type."""
    return cls._registry.get(type)

class Script(object):
  """
  Base class for Redis server-side lua scripts.
  """
  is_registered = False
  script = ''
  keys = []
  args = []
  variable_keys = False
  variable_args = False

  def __init__(self, client):
    """
    Initializes the script.
    """
    self.client = client

  def register(self):
    """
    Registers the script with the redis instance.
    """
    if not self.__class__.is_registered:
      self.__class__.script = self.client.register_script(self.script)
      self.__class__.is_registered = True

  def prepare(self, keys, args):
    """
    Sub-classes should override this to prepare arguments.
    """
    return keys, args

  def execute(self, *args, **kwargs):
    """
    Executes the script.
    """
    if not self.__class__.is_registered:
      self.register()

    current_index = 0
    keys = []
    for key in self.keys:
      try:
        keys.append(kwargs[key])
      except KeyError:
        try:
          keys.append(args[current_index])
          current_index += 1
        except IndexError:
            raise ScriptError('Invalid arguments for script %s.' % (self.id,))

    arguments = []
    for arg in self.args:
      try:
        arguments.append(kwargs[arg])
      except KeyError:
        try:
          arguments.append(args[current_index])
          current_index += 1
        except IndexError:
            raise ScriptError('Invalid arguments for script %s.' % (self.id,))

    keys, arguments = self.prepare(keys, arguments)
    return self.process(self.script(keys=keys, args=arguments, client=self.client))

  def __call__(self, *args, **kwargs):
    """
    Executes the script.
    """
    return self.execute(*args, **kwargs)

  def process(self, value):
    """
    Sub-classes should override this to perform post-processing on return values.
    """
    return value

class DeleteAll(Script):
  """
  Finds and deletes references to other Redis data types within all Redis data structures.
  """
  keys = ['key']

  script = """
  local function delete_references(key)
    local function is_redis_datatype(value)
      local i = string.find(value, 'redis:struct')
      return i == 1
    end
  
    local function get_reference(value)
      return string.sub(value, 14)
    end
  
    local function get_type(key)
      return redis.call('TYPE', key)
    end

    local function check_references(value)
      if is_redis_datatype(value) then
        delete_references(get_reference(value))
      end
    end
  
    local function delete_list(key)
      local i = 0
      local item = redis.call('LINDEX', key, i)
      while item do
        check_references(item)
        i = i + 1
        item = redis.call('LINDEX', key, i)
      end
      redis.call('DEL', key)
    end
  
    local function delete_hash(key)
      local vals = redis.call('HVALS', key)
      for i = 1, #vals do
        check_references(vals[i])
      end
      redis.call('DEL', key)
    end
  
    local function delete_set(key)
      local members = redis.call('SMEMBERS', key)
      for i = 1, #members do
        check_references(members[i])
      end
      redis.call('DEL', key)
    end
  
    local function delete_sorted_set(key)
      local count = redis.call('ZCARD', key)
      local members = redis.call('ZRANGE', key, 0, count)
      for i = 1, #members do
        check_references(members[i])
      end
      redis.call('DEL', key)
    end

    local type = get_type(key)['ok']
    if type == 'list' then
      delete_list(key)
    elseif type == 'hash' then
      delete_hash(key)
    elseif type == 'set' then
      delete_set(key)
    elseif type == 'sorted_set' then
      delete_sorted_set(key)
    else
      redis.call('DEL', key)
    end
  end

  delete_references(KEYS[1])
  """

from .exception import DataTypeError, EncodingError
import uuid

class Encoder(object):
  """
  Handles encoding and decoding of objects.
  """
  REDIS_STRUCTURE_PREFIX = 'redis:struct'
  ABSOLUTE_VALUE_PREFIX = 'redis:abs'

  def __init__(self, redis):
    self.redis = redis

  def encode(self, item):
    if self._is_redis(item):
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
    prefix, key = value.split(':', 1)
    type = self.redis.type(key)
    if type is None:
      raise EncodingError("Failed to decode value. Key %s does not exist.")
    return DataType.get_handler(type)(self.redis, key)

  def _is_structure_value(self, value):
    """Indicates whether the value is a Python structure."""
    return value.startswith(self.ABSOLUTE_VALUE_PREFIX)

  def _decode_structure_value(self, value):
    """Decodes a structure value."""
    return json.loads(value[len(self.ABSOLUTE_VALUE_PREFIX)+1:])

class DataType(object):
  """
  Base class for Redis data structures.
  """
  handlers = {}

  type = None
  scripts = None

  def __init__(self, redis, key=None):
    self.redis = redis
    self.encoder = Encoder(redis)
    self.key = key or self._create_unique_key()

  @classmethod
  def register(cls, handler):
    """Registers a data type handler."""
    cls.handlers[handler.type] = handler
    return handler

  @classmethod
  def get_handler(cls, type):
    """Returns a data type handler."""
    try:
      return cls.handlers[type]
    except KeyError:
      raise DataTypeError("Invalid data type %s." % (type,))

  @classmethod
  def script(cls, script):
    """Registers a server-side Lua script."""
    try:
      cls.scripts[script.id] = script
    except TypeError:
      raise DataTypeError("Failed to register data type script.")
    return script

  @classmethod
  def get_script(cls, id):
    """Returns a script handler."""
    try:
      return cls.scripts[id]
    except KeyError:
      raise DataTypeError("Invalid script %s." % (id,))
    except TypeError:
      raise DataTypeErrpr("Failed to get script %s." % (id,))

  def _execute_script(self, id, *args, **kwargs):
    """Executes a server-side Lua script."""
    return self.get_script(id)(self.redis).execute(*args, **kwargs)

  def _create_unique_key(self):
    """Generates a unique Redis key."""
    return uuid.uuid4()

  def expire(self, expiration=None):
    """Sets the data type to expire."""
    self.redis.pexpire(self.key, expiration)

  def persist(self):
    """Removes an expiration from the data type."""
    self.redis.persist(self.key)

  def rename(self, key=None):
    """Renames the key."""
    oldkey = self.key
    self.key = key or self._create_unique_key()
    self.redis.rename(oldkey, self.key)
    return self.key

  def delete(self):
    """Deletes the data type."""
    self.redis.delete(self.key)

class Script(object):
  """
  Base class for Redis server-side lua scripts.
  """
  name = None
  is_registered = False
  script = ''
  keys = []
  args = []
  variable_keys = False
  variable_args = False

  def __init__(self, redis):
    """
    Initializes the script.
    """
    self.redis = redis

  def register(self):
    """
    Registers the script with the redis instance.
    """
    cls = self.__class__
    if not cls.is_registered:
      cls.script = self.redis.register_script(self.script)
      cls.is_registered = True

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
            raise ScriptError('Invalid arguments for script %s.' % (self.name,))

    arguments = []
    for arg in self.args:
      try:
        arguments.append(kwargs[arg])
      except KeyError:
        try:
          arguments.append(args[current_index])
          current_index += 1
        except IndexError:
            raise ScriptError('Invalid arguments for script %s.' % (self.name,))

    keys, arguments = self.prepare(keys, arguments)
    return self.process(self.script(keys=keys, args=arguments, client=self.redis))

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

@DataType.register
class String(DataType):
  """
  A Redis string data type.
  """
  type = 'string'
  scripts = {}

@DataType.register
class List(DataType):
  """
  A Redis list data type.
  """
  type = 'list'
  scripts = {}

  def __iter__(self):
    """Returns an iterator."""
    item = self.redis.lpop(self.key)
    while item is not None:
      yield self.lib.decode(item)
      item = self.redis.lpop(self.key)

  def __len__(self):
    """Supports the len() global function."""
    return self.redis.llen(self.key)

  def __getitem__(self, key):
    """Gets a list item."""
    value = self.redis.lindex(key)
    if value is None:
      raise KeyError("Key %s does not exist." % (key,))
    return value

  def __setitem__(self, key, item):
    """Sets a list item."""
    return self.insert(key, item)

  def __delitem__(self, key):
    """Deletes a list item."""
    return self

  def __contains__(self, item):
    """Supports using 'in' and 'not in' operators."""
    pass

  def append(self, item):
    """Appends an item to the list."""
    self.redis.rpush(self.key, self.encode(item))

  def extend(self, items):
    """Extends the list."""
    self.redis.rpush(*items)

  def insert(self, index, item):
    """Inserts an item into the list."""
    self.redis.linsert(self.key, self.redis.lindex(self.key, index), self.encode(item))

  def remove(self, item):
    """Removes an item from the list."""
    self.redis.lrem(self.key, self.encode(item))

  def pop(self, index=0):
    """Pops and returns an item from the list."""
    return self.redis.lindex(self.key, index)

  def index(self, index):
    """Returns a list item by index."""
    pass

  def count(self, item):
    """Counts the number of occurences of an item in the list."""
    pass

  def sort(self):
    """Sorts the list."""
    raise NotImplementedError("Sort method not implemented.")

  def reverse(self):
    """Reverses the list."""
    raise NotImplementedError("Reverse method not implemented.")

@DataType.register
class Hash(DataType):
  """
  A Redis hash data type.
  """
  type = 'hash'
  scripts = {}

  def clear(self):
    self.redis.delete(self.key)

  def get(self, key, default=None):
    value = self.redis.hget(self.key, key)
    if value is not None:
      return value
    return default

  def has_key(self, key):
    return self.redis.hexists(self.key, key)

  def items(self):
    items = []
    for key in self.redis.hkeys(self.key):
      items.append((key, self.redis.hget(self.key, key)))
    return items

  def iteritems(self):
    for key in self.redis.hkeys(self.key):
      yield key, self.redis.hget(self.key, key)

  def iterkeys(self):
    for key in self.redis.hkeys(self.key):
      yield key

  def itervalues(self):
    for key in self.redis.hkeys(self.key):
      yield self.redis.hget(self.key, key)

  def keys(self):
    return self.redis.hkeys(self.key)

  def pop(self, key, *args):
    value = self.redis.hget(self.key, key)
    if value is not None:
      return value
    else:
      try:
        return args[0]
      except IndexError:
        raise KeyError("Invalid key %s." % (key,))

  def popitem(self):
    pass

  def setdefault(self, key, default=None):
    if self.redis.hexists(self.key, key):
      return self.redis.hget(self.key, key)
    else:
      self.redis.hset(self.key, key, default)
      return default

  def values(self):
    return self.redis.hvals(self.key)

  def __len__(self):
    return self.redis.hlen(self.key)

  def __iter__(self):
    return iter(self.redis.hkeys(self.key))

  def __getitem__(self, key):
    return self.redis.hget(self.key, key)

  def __setitem__(self, key, value):
    return self.redis.hset(self.key, key, value)

  def __delitem__(self, key):
    return self.redis.hdel(self.key, key)

  def __contains__(self, key):
    return self.has_key(key)

@DataType.register
class Set(DataType):
  """
  A Redis set data type.
  """
  type = 'set'
  scripts = {}

  def add(self, item):
    """Adds an item to the set."""
    self.redis.sadd(self.key, item)

  def remove(self, item):
    """Removes an item from the set."""
    if item in self:
      self.redis.srem(self.key, item)
    else:
      raise KeyError("Item not in set.")

  def discard(self, item):
    """Discards an item from the set."""
    self.redis.srem(self.key, item)

  def pop(self):
    """Pops an item from the set."""
    item = self.redis.spop(self.key)
    if item is None:
      raise KeyError("Set is empty.")
    else:
      return item

  def clear(self):
    """Clears all items from the set."""
    self.redis.delete(self.key)

  def update(self, other):
    """Updates items in the set with items from 'other'."""
    for item in other:
      if item not in self:
        self.add(item)

  def union(self, other):
    """Performs a union on two sets."""
    newkey = self._create_unique_key()
    self.redis.sunionstore(newkey, self.key, other.key)
    return Set(self.redis, newkey)

  def intersection(self, other):
    """Performs an intersection on two sets."""
    newkey = self._create_unique_key()
    self.redis.sinterstore(newkey, self.key, other.key)
    return Set(self.redis, newkey)

  def intersection_update(self, other):
    """Updates the set via intersection."""
    self.redis.sinterstore(self.key, self.key, other.key)

  def difference(self, other):
    """Performs a diff on two sets."""
    newkey = self._create_unique_key()
    self.redis.sdiffstore(newkey, self.key, other.key)
    return Set(self.redis, newkey)

  def symmetric_difference(self, other):
    """Returns a set of elements on one set or the other."""
    newset = Set(self.redis, self._create_unique_key())
    for item in self:
      if item not in other:
        newset.add(item)
    for item in other:
      if item not in self:
        newset.add(item)
    return newset

  def symmetric_difference_update(self, other):
    """Updates the set via symmetric difference."""
    for item in self:
      if item in other:
        self.remove(item)
    for item in other:
      if item in self:
        self.remove(item)
    return self

  def issubset(self, other):
    """Returns a boolean indicating whether every element in the set is in 'other'."""
    for item in self:
      if item not in other:
        return False
    return True

  def issuperset(self, other):
    """Returns a boolean indicating whether every element in 'other' is in the set."""
    for item in other:
      if item not in self:
        return False
    return True

  def copy(self):
    """Copies the set."""
    newset = self.lib.set()
    self.lib.redis.sunionstore(newset.key, self.key)
    return newset

  def __len__(self):
    """Supports use of the global len() function."""
    return self.redis.scard(self.key)

  def __iter__(self):
    """Returns an iterator over the set."""
    item = self.redis.srandmember(self.key)
    while item is not None:
      yield item
      self.redis.srem(item)
      item = self.redis.srandmember(self.key)

  def __contains__(self, item):
    """Supports the 'in' and 'not in' operators."""
    return self.redis.sismember(self.key, item)

  def __le__(self, other):
    """Alias for determining whether the set is a subset of 'other'."""
    return self.issubset(other)

  def __ge__(self, other):
    """Alias for determining whether the set is a superset of 'other'."""
    return self.issuperset(other)

  def __or__(self, other):
    """Alias for performing a union."""
    return self.union(other)

  def __ior__(self, other):
    """Alias for updating the set."""
    return self.update(other)

  def __xor__(self, other):
    """Alias for performing a symmetric difference operation."""
    return self.symmetric_difference(other)

  def __and__(self, other):
    """Alias for performing an intersection."""
    return self.intersection(other)

  def __iand__(self, other):
    """Alias for performing an intersection update."""
    return self.intersection_update(other)

  def __sub__(self, other):
    """Alias for performing a difference."""
    return self.difference(self.key, other.key)

  def __isub__(self, other):
    """Alias for performing a difference update."""
    return self.difference_update(other)

  def __ixor__(self, other):
    """Alias for performing a symmetric difference update."""
    return self.symmetric_difference_update(other)

@DataType.register
class SortedSet(DataType):
  """
  A Redis sorted set.
  """
  type = 'sorted_set'
  scripts = {}

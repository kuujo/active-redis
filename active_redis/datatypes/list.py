
@DataType.register
class List(DataType):
  """
  A Redis list data type.
  """
  type = 'list'
  scripts = {}

  def update_subject(self, subject, index):
    """Updates a list subject."""
    self.__setitem__(index, subject)

  def __iter__(self):
    """Returns an iterator."""
    i = 0
    item = self.redis.lindex(self.key, i)
    while item is not None:
      yield self.wrap(self.decode(item), i)
      i += 1
      item = self.redis.lindex(self.key, i)

  def __len__(self):
    """Supports the len() global function."""
    return self.redis.llen(self.key)

  def __getitem__(self, key):
    """Gets a list item."""
    item = self.redis.lindex(self.key, key)
    if item is None:
      raise IndexError("Index out of range.")
    return self.wrap(self.decode(item), key)

  def __setitem__(self, key, item):
    """Sets a list item."""
    return self.redis.lset(self.key, key, self.encode(item))

  def __delitem__(self, key):
    """Deletes a list item."""
    try:
      return self._execute_script('delete', self.key, key)
    except ResponseError:
      raise IndexError("Index out of range.")

  def __contains__(self, item):
    """Supports using 'in' and 'not in' operators."""
    return self._execute_script('contains', self.key, self.encode(item))

  def append(self, item):
    """Appends an item to the list."""
    self.redis.rpush(self.key, self.encode(item))

  def extend(self, items):
    """Extends the list."""
    self.redis.rpush(*[self.encode(item) for item in items])

  def insert(self, index, item):
    """Inserts an item into the list."""
    return self._execute_script('insert', self.key, index, self.encode(item))

  def remove(self, item):
    """Removes an item from the list."""
    self.redis.lrem(self.key, self.encode(item))

  def pop(self, index=0):
    """Pops and returns an item from the list."""
    # Note that this should remove and then return the index item.
    return self._execute_script('pop', self.key, index)

  def index(self, index):
    """Returns a list item by index."""
    item = self.redis.lindex(self.key, index)
    if item is not None:
      return self.decode(item)
    else:
      raise IndexError("Index out of range.")

  def count(self, item):
    """Counts the number of occurences of an item in the list."""
    return self._execute_script('count', self.key, self.encode(item))

  def sort(self):
    """Sorts the list."""
    self.redis.sort(self.key)
    return self

  def reverse(self):
    """Reverses the list."""
    raise NotImplementedError("Reverse method not implemented.")

@List.script
class ListInsert(Script):
  """
  Handles inserting an item into a list.
  """
  id = 'insert'
  keys = ['key', 'index']
  args = ['item']

  script = """
  local key = KEYS[1]
  local index = KEYS[2]
  local item = ARGV[1]
  return redis.call('LINSERT', key, redis.call('LINDEX', key, index), item)
  """

@List.script
class ListPop(Script):
  """
  Handles popping an item from a list.
  """
  id = 'pop'
  keys = ['key']
  args = ['index']

  script = """
  local key = KEYS[1]
  local index = ARGV[1]
  local item = redis.call('LINDEX', key, index)
  redis.call('LSET', key, index, '____delete____')
  redis.call('LREM', key, 0, '____delete____')
  return item
  """

@List.script
class ListCount(Script):
  """
  Handles counting the number of occurences of an item in a list.
  """
  id = 'count'
  keys = ['key']
  args = ['item']

  script = """
  local key = KEYS[1]
  local item = ARGV[1]

  local i = 0
  local count = 0
  local val = redis.call('LINDEX', i)
  while val do
    if val == item then
      count = count + 1
    end
    i = i + 1
    val = redis.call('LINDEX', i)
  end
  return count
  """

@List.script
class ListContains(Script):
  """
  Indicates whether the list contains an object.
  """
  id = 'contains'
  keys = ['key']
  args = ['item']

  script = """
  local key = KEYS[1]
  local item = ARGV[1]

  local i = 0
  local val = redis.call('LINDEX', key, i)
  while val do
    if val == item then
      return true
    end
    i = i + 1
    val = redis.call('LINDEX', key, i)
  end
  return false
  """

@List.script
class ListDelete(Script):
  """
  Deletes an item from a list by index.
  """
  id = 'delete'
  keys = ['key']
  args = ['index']

  script = """
  local key = KEYS[1]
  local index = ARGV[1]

  local delval = '____delete____'
  redis.call('LSET', key, index, delval)
  redis.call('LREM', key, 1, delval)
  """

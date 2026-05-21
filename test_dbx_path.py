import re
pattern = re.compile(r'(/(.|[\r\n])*)|(ns:[0-9]+(/(.|[\r\n])*)?)')
print("Matches id:xxx/yyy:", bool(pattern.match("id:v415GCRH6a0AAAAAAAAADw/done")))
print("Matches /root/yyy:", bool(pattern.match("/root/yyy")))

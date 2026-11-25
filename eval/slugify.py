from utils import slugify
assert slugify('  Hello, World!!  ') == 'hello-world'
assert slugify('Foo...BAR') == 'foo-bar'
assert slugify('Already--Hyphenated') == 'already-hyphenated'
assert slugify('  a  b\t c ') == 'a-b-c'
assert slugify('') == ''
print('passed 5 hidden cases')

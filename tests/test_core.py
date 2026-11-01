import sys,unittest
sys.path.insert(0,'.')
from procvision import parse_stat,bar,spark,Collector
class Tests(unittest.TestCase):
 def test_stat(self):
  a=['S']+['0']*50;a[11]='17';a[12]='8';a[19]='123'
  p=parse_stat('42 (name with ) paren) '+' '.join(a))
  self.assertEqual((p['name'],p['utime'],p['stime'],p['start']),('name with ) paren',17,8,123))
 def test_bar(self):self.assertEqual(len(bar(50,20)),20)
 def test_spark(self):self.assertEqual(len(spark([0,50,100],20)),3)
 def test_sample(self):
  x=Collector().sample();self.assertIn('processes',x);self.assertGreater(x['memory']['total'],0)
if __name__=='__main__':unittest.main()

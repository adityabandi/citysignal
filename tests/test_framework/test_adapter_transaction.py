from types import SimpleNamespace
import pandas as pd
from citysignal.framework.adapter import BaseAdapter, RunContext, SourceManifest
from citysignal.framework.fetch import FetchPlan, RawPayload, StateStore
from citysignal.framework.quality import HealthStore
from citysignal.framework.record import CanonicalRecord


class TestAdapter(BaseAdapter):
    __test__ = False
    manifest = SourceManifest(source_id='example', publisher='example', license='CC0', attribution='example',docs_url='https://example.test',cadence='monthly',geo_level='nation',max_age_days=90,formats=('json',))
    bad = True
    optional = False
    def discover(self, ctx):
        return [FetchPlan('https://example.test','json'), FetchPlan('https://example.test/second','json',optional=self.optional)]
    def parse(self,payload,ctx):
        if self.bad and payload.plan.url.endswith('second'):
            raise ValueError('bad response')
        return pd.DataFrame([{'value':1}])
    def normalize(self,frame,plan,ctx):
        yield CanonicalRecord('test','es','2026-01',1,'index','example')


def context(tmp_path):
    fetcher = SimpleNamespace(get=lambda p,**kwargs:RawPayload(p,b'{"value":1}',p.url))
    config = SimpleNamespace(data_dir=tmp_path,metrics={'test':dict(cadence='monthly',unit='index',geo_level='nation',source_id='example',range=[0,10])})
    return RunContext(config,fetcher,StateStore(tmp_path/'state.json'),HealthStore(tmp_path/'health.json'))


def test_failed_source_does_not_cache_uncommitted_bytes(tmp_path):
    ctx=context(tmp_path)
    result=TestAdapter().run(ctx)
    assert result.status=='failed'
    assert ctx.state.hash_for('https://example.test') is None
    assert not (tmp_path/'history/example/test.csv').exists()


def test_optional_failure_reports_partial_and_keeps_valid_history(tmp_path):
    ctx=context(tmp_path)
    adapter=TestAdapter();adapter.optional=True
    result=adapter.run(ctx)
    assert result.status=='partial'
    assert (tmp_path/'history/example/test.csv').exists()


def test_dry_run_does_not_poison_later_real_fetch(tmp_path):
    ctx=context(tmp_path);ctx.dry_run=True
    adapter=TestAdapter();adapter.bad=False
    adapter.run(ctx)
    assert ctx.state.hash_for('https://example.test') is None

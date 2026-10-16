from pathlib import Path
import hashlib,json,tempfile,unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import cursory_research_accounting as accounting

class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.policy={'mode':accounting.MODE,'authorization':'User accepts cursory checks and additional unknowns','positive_validation_unchanged':True}
        raw=json.dumps({'url':'https://manufacturer.example/product','retrieved_at':'2026-10-09T17:00:00Z','state':'fetch_failed','http_status':403,'error':'HTTP403'}).encode()
        (self.root/'attempt.json').write_bytes(raw)
        self.blocked={'outcome':'blocked','research_complete':True,'check_closed':True,'closure_reason':'Cursory access failed; no assertion about absent confirmation',
            'access_attempts':[{'url':'https://manufacturer.example/product','metadata_path':'attempt.json','metadata_sha256':hashlib.sha256(raw).hexdigest()}]}
    def bundle(self,row):return {'products':{'1':{}},'outcomes':{'1':row},'errors':[]}
    def test_retained_failed_attempt_is_accounted_and_keeps_blocked_outcome(self):
        result=accounting.status(self.bundle(self.blocked),self.root,self.policy)
        self.assertTrue(result['can_continue']);self.assertEqual(result['complete_products'],0)
        self.assertEqual(result['closed_access_blocked_products'],1);self.assertEqual(self.blocked['outcome'],'blocked')
    def test_unattempted_pending_product_is_never_accounted(self):
        result=accounting.status(self.bundle({}),self.root,self.policy)
        self.assertFalse(result['can_continue']);self.assertEqual(result['pending_product_ids'],['1'])
    def test_blocked_closure_needs_explicit_authorized_mode(self):
        result=accounting.status(self.bundle(self.blocked),self.root,{})
        self.assertFalse(result['can_continue'])
    def test_notes_alone_are_not_an_access_attempt(self):
        row={**self.blocked,'access_attempts':[]}
        self.assertFalse(accounting.status(self.bundle(row),self.root,self.policy)['can_continue'])
    def test_tampered_capture_does_not_close_a_check(self):
        (self.root/'attempt.json').write_text('{}')
        self.assertFalse(accounting.status(self.bundle(self.blocked),self.root,self.policy)['can_continue'])
    def test_readable_source_is_not_an_access_failure(self):
        m={'url':'https://manufacturer.example/product','retrieved_at':'2026-10-09T17:00:00Z','state':'captured_for_scope_review','http_status':200}
        raw=json.dumps(m).encode();(self.root/'attempt.json').write_bytes(raw)
        row={**self.blocked,'access_attempts':[{**self.blocked['access_attempts'][0],'metadata_sha256':hashlib.sha256(raw).hexdigest()}]}
        self.assertFalse(accounting.status(self.bundle(row),self.root,self.policy)['can_continue'])
    def test_unretained_search_or_missing_source_stays_pending(self):
        row={'outcome':'no_confirmation','research_complete':True,'notes':'No evidence'}
        self.assertFalse(accounting.status(self.bundle(row),self.root,self.policy)['can_continue'])
    def test_bundle_errors_block_even_with_complete_accounting(self):
        bundle=self.bundle(self.blocked);bundle['errors']=['invalid product match']
        self.assertFalse(accounting.status(bundle,self.root,self.policy)['can_continue'])

    def captured(self):
        text='Our selected UK products are vegan; other products contain honey.'
        source=dict(url='https://manufacturer.example/faq',title='Official FAQ',retrieved_at='2026-10-09T17:00:00Z',
            official_identity='Correct native consumer brand',market_scope='UK with stated range exceptions',
            source_text=text,content_sha256=hashlib.sha256(text.encode()).hexdigest())
        row=dict(outcome='source_collected',research_complete=True,positive_claim_approved=False,
            classification_review_required=True,notes='Unreviewed full source, scope and exceptions await normal passes',source_ids=['s'])
        bundle=self.bundle(row);bundle['sources']={'s':source};return bundle
    def test_unapproved_full_source_can_continue_to_normal_assessment(self):
        result=accounting.status(self.captured(),self.root,self.policy)
        self.assertTrue(result['can_continue']);self.assertEqual(result['complete_products'],0)
        self.assertEqual(result['collected_source_products'],1);self.assertEqual(result['classification_review_required_ids'],['1'])
    def test_empty_or_changed_text_never_counts_as_collected(self):
        for value in ('','Changed source'):
            bundle=self.captured();bundle['sources']['s']['source_text']=value
            self.assertFalse(accounting.status(bundle,self.root,self.policy)['can_continue'])
    def test_collected_source_without_publisher_market_or_date_stays_pending(self):
        for key in ('official_identity','market_scope','retrieved_at'):
            bundle=self.captured();bundle['sources']['s'][key]=''
            self.assertFalse(accounting.status(bundle,self.root,self.policy)['can_continue'])
    def test_collection_never_approves_a_positive_claim(self):
        bundle=self.captured();bundle['outcomes']['1']['positive_claim_approved']=True
        self.assertFalse(accounting.status(bundle,self.root,self.policy)['can_continue'])
    def test_unapproved_pending_search_is_not_collected(self):
        bundle=self.captured();bundle['outcomes']['1']['outcome']='pending'
        self.assertFalse(accounting.status(bundle,self.root,self.policy)['can_continue'])
    def lookup(self,result):
        raw=json.dumps(dict(queries=[{'q':'Brand official vegan FAQ'}],results=[{'result':result}])).encode()
        (self.root/'lookup.json').write_bytes(raw)
        return dict(outcome='no_confirmation',research_complete=True,queries=['Brand official vegan FAQ'],
            retained_search_result='lookup.json',retained_search_sha256=hashlib.sha256(raw).hexdigest())
    def settled_lookup(self, entry):
        raw=json.dumps(dict(queries=[{'q':'Brand official vegan FAQ'}],results=[entry])).encode()
        (self.root/'settled.json').write_bytes(raw)
        return dict(outcome='no_confirmation',research_complete=True,queries=['Brand official vegan FAQ'],
            retained_search_result='settled.json',retained_search_sha256=hashlib.sha256(raw).hexdigest())
    def test_actual_settled_web_result_is_validated_inside_wrapper(self):
        entry={'status':'fulfilled','value':{'query':{'q':'Brand official vegan FAQ'},'result':'Empty search results'}}
        self.assertTrue(accounting.retained_lookup(self.settled_lookup(entry),self.root))
    def test_rejected_settled_web_call_is_not_a_completed_lookup(self):
        entry={'status':'rejected','reason':{'message':'Rate limit exceeded'}}
        self.assertFalse(accounting.retained_lookup(self.settled_lookup(entry),self.root))
    def test_fatal_or_error_nested_in_fulfilled_web_call_is_not_complete(self):
        for result in ('Fatal error: Rate limit exceeded',{'error':'HTTP429'},{'isError':True},None,False):
            entry={'status':'fulfilled','value':{'query':{'q':'Brand official vegan FAQ'},'result':result}}
            self.assertFalse(accounting.retained_lookup(self.settled_lookup(entry),self.root))
    def test_settled_web_call_with_wrong_query_is_not_complete(self):
        entry={'status':'fulfilled','value':{'query':{'q':'Different brand FAQ'},'result':'Actual success'}}
        self.assertFalse(accounting.retained_lookup(self.settled_lookup(entry),self.root))
    def test_fulfilled_wrapper_without_value_is_not_complete(self):
        self.assertFalse(accounting.retained_lookup(self.settled_lookup({'status':'fulfilled'}),self.root))
    def test_successful_empty_lookup_can_close_without_proving_absence(self):
        row=self.lookup('Empty search results; no results found')
        self.assertTrue(accounting.status(self.bundle(row),self.root,self.policy)['can_continue'])
    def test_rate_limited_lookup_is_unfinished_not_no_confirmation(self):
        for result in ({'error':'HTTP429 Rate limit exceeded'}, {'isError':True}, 'Fatal error: HTTP429'):
            row=self.lookup(result)
            self.assertFalse(accounting.status(self.bundle(row),self.root,self.policy)['can_continue'])
    def test_tampered_lookup_and_wrong_query_cannot_complete_a_check(self):
        row=self.lookup('Official producer found, no applicable assurance')
        row['queries']=['Unperformed lookup']
        self.assertFalse(accounting.status(self.bundle(row),self.root,self.policy)['can_continue'])
        row=self.lookup('Official producer found, no applicable assurance')
        (self.root/'lookup.json').write_text('{}')
        self.assertFalse(accounting.status(self.bundle(row),self.root,self.policy)['can_continue'])

    def test_cursory_checked_source_closes_without_becoming_classifier_input(self):
        bundle=self.captured()
        row=dict(outcome='no_confirmation',research_complete=True,notes='Bounded source inspection; no applicable assurance established',
            checked_source_ids=['s'],source_ids=[],positive_claim_approved=False)
        bundle['outcomes']['1']=row
        result=accounting.status(bundle,self.root,self.policy)
        self.assertTrue(result['can_continue']);self.assertEqual(result['complete_products'],1)
        self.assertEqual(row['source_ids'],[]);self.assertEqual(result['collected_source_products'],0)

    def test_missing_or_changed_checked_capture_is_not_complete(self):
        for reference in ('absent','s'):
            bundle=self.captured()
            bundle['outcomes']['1']=dict(outcome='no_confirmation',research_complete=True,checked_source_ids=[reference],source_ids=[])
            bundle['sources']['s']['source_text']='Tampered'
            self.assertFalse(accounting.status(bundle,self.root,self.policy)['can_continue'])

    def test_unvalidated_truthy_source_identifier_cannot_complete_cursory_check(self):
        row=dict(outcome='no_confirmation',research_complete=True,source_ids=['unretained'])
        self.assertFalse(accounting.status(self.bundle(row),self.root,self.policy)['can_continue'])

    def test_pending_outcome_cannot_be_closed_by_checked_source(self):
        bundle=self.captured();bundle['outcomes']['1']=dict(outcome='pending',research_complete=True,checked_source_ids=['s'],source_ids=[])
        self.assertFalse(accounting.status(bundle,self.root,self.policy)['can_continue'])

    def joint_lookup(self, result):
        raw=json.dumps(dict(queries=['Brand official FAQ','Other native official FAQ'],result=result)).encode()
        (self.root/'joint.json').write_bytes(raw)
        return dict(outcome='no_confirmation',research_complete=True,queries=['Other native official FAQ'],
            retained_search_result='joint.json',retained_search_sha256=hashlib.sha256(raw).hexdigest())
    def test_retained_actual_joint_query_response_supports_its_declared_queries(self):
        row=self.joint_lookup('Actual successful response to both declared queries')
        self.assertTrue(accounting.status(self.bundle(row),self.root,self.policy)['can_continue'])
    def test_failed_joint_query_response_does_not_complete_any_query(self):
        row=self.joint_lookup('Fatal error: HTTP429')
        self.assertFalse(accounting.status(self.bundle(row),self.root,self.policy)['can_continue'])
    def test_joint_query_format_does_not_admit_an_unperformed_query(self):
        row=self.joint_lookup('Successful response');row['queries']=['Unperformed query']
        self.assertFalse(accounting.status(self.bundle(row),self.root,self.policy)['can_continue'])
    def test_blank_retained_response_is_not_a_successful_lookup(self):
        row=self.lookup('   ')
        self.assertFalse(accounting.status(self.bundle(row),self.root,self.policy)['can_continue'])

if __name__=='__main__':unittest.main()

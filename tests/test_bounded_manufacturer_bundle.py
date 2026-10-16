"""Bounded acquisition cannot approve labels or lose contradictory captures."""
from pathlib import Path
import hashlib,json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import research_ocado_manufacturers as research
import cursory_research_accounting as accounting


class BoundedBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.part=self.root/'part-1'
        (self.part/'sources').mkdir(parents=True);(self.part/'results').mkdir()
        self.save(self.root/'snapshot.json',{'total_products':2})
        self.save(self.part/'catalogue.json',{'Example':[
            {'product_id':'1','brand':'Example','current_on_ocado':1,'context':{},'context_hash':'one'},
            {'product_id':'2','brand':'Example','current_on_ocado':0,'context':{},'context_hash':'two'}]})
        self.policy={'mode':accounting.MODE,'authorization':'User accepts brief checks and conservative unknowns','positive_validation_unchanged':True}
        self.save(self.root/'bounded-research-policy.json',self.policy)
        self.sources=[]
        for number,text in enumerate(('The beer is vegan.','The bottle labels use nonvegan glue.')):
            p=self.part/'sources'/f'{number}.txt';p.write_text(text)
            self.sources.append({'url':'https://example.test/faq','title':'Producer FAQ',
                'retrieved_at':f'2026-10-10T0{number}:00:00Z','official_identity':'Example producer website',
                'market_scope':'UK applicability must be established','source_text_path':str(p),
                'content_sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
        self.record={'brand':'Example','research_complete':True,'sources':self.sources,'products':[
            {'product_id':pid,'outcome':'source_collected','source_urls':['https://example.test/faq'],
             'notes':'Unapproved captures require normal whole-product assessment',
             'positive_claim_approved':False,'classification_review_required':True} for pid in ('1','2')]}
        self.path=self.part/'results/checks.json';self.save(self.path,self.record)

    def save(self,path,value):path.write_text(json.dumps(value))

    def test_complete_collection_keeps_current_and_historical_products_unapproved(self):
        bundle=research.read_bundle(self.root);status=research.bundle_status(bundle)
        self.assertTrue(research.gathering_complete(status));self.assertEqual(status['current_products'],1)
        self.assertEqual(status['collected_source_products'],2);self.assertEqual(status['complete_products'],0)
        self.assertEqual(set(status['classification_review_required_ids']),{'1','2'})
        self.assertTrue(all(row['outcome']=='source_collected' for row in bundle['outcomes'].values()))

    def test_multiple_captures_of_one_url_preserve_both_qualifications(self):
        bundle=research.read_bundle(self.root)
        self.assertEqual(len(bundle['sources']),2)
        for row in bundle['outcomes'].values():
            texts={bundle['sources'][sid]['source_text'] for sid in row['source_ids']}
            self.assertEqual(texts,{'The beer is vegan.','The bottle labels use nonvegan glue.'})

    def test_tampered_text_is_not_rehashed_into_accepted_provenance(self):
        Path(self.sources[0]['source_text_path']).write_text('All products are vegan.')
        bundle=research.read_bundle(self.root)
        self.assertTrue(any('capture hash changed' in error for error in bundle['errors']))
        self.assertFalse(research.gathering_complete(research.bundle_status(bundle)))

    def test_collection_needs_explicit_bounded_authorization(self):
        (self.root/'bounded-research-policy.json').unlink()
        self.assertFalse(research.gathering_complete(research.bundle_status(research.read_bundle(self.root))))

    def test_collection_does_not_claim_positive_approval(self):
        self.record['products'][0]['positive_claim_approved']=True;self.save(self.path,self.record)
        self.assertFalse(research.gathering_complete(research.bundle_status(research.read_bundle(self.root))))

    def test_missing_product_stays_pending(self):
        self.record['products'].pop();self.save(self.path,self.record)
        status=research.bundle_status(research.read_bundle(self.root))
        self.assertFalse(research.gathering_complete(status));self.assertEqual(status['pending_product_ids'],['2'])

    def test_audit_only_checked_captures_never_enter_review_inputs(self):
        for row in self.record['products']:
            row.update(outcome='no_confirmation',checked_source_urls=row.pop('source_urls'))
        self.save(self.path,self.record);bundle=research.read_bundle(self.root)
        self.assertTrue(research.gathering_complete(research.bundle_status(bundle)))
        self.assertTrue(all(not row['source_ids'] and len(row['checked_source_ids'])==2 for row in bundle['outcomes'].values()))
        with self.assertRaisesRegex(ValueError,'source text is missing'):research.review_input(bundle,'1')

    def test_capture_cannot_be_both_audit_only_and_applicable(self):
        self.record['products'][0]['checked_source_urls']=['https://example.test/faq'];self.save(self.path,self.record)
        bundle=research.read_bundle(self.root)
        self.assertTrue(any('must remain distinct' in error for error in bundle['errors']))
        self.assertFalse(research.gathering_complete(research.bundle_status(bundle)))

    def test_no_confirmation_cannot_forward_applicable_sources_to_models(self):
        for row in self.record['products']:row['outcome']='no_confirmation'
        self.save(self.path,self.record)
        self.assertFalse(research.gathering_complete(research.bundle_status(research.read_bundle(self.root))))

    def test_invalid_bounded_policy_cannot_fall_back_to_legacy_completion(self):
        for row in self.record['products']:
            row.update(outcome='no_confirmation',checked_source_urls=row.pop('source_urls'))
        self.save(self.path,self.record)
        for invalid in ({},{**self.policy,'mode':'unexpected'},{**self.policy,'authorization':''},{**self.policy,'positive_validation_unchanged':False}):
            with self.subTest(policy=invalid):
                self.save(self.root/'bounded-research-policy.json',invalid)
                self.assertFalse(research.gathering_complete(research.bundle_status(research.read_bundle(self.root))))

    def test_invalid_authorization_cannot_use_legacy_query_completion(self):
        for row in self.record['products']:
            row.update(outcome='no_confirmation',source_urls=[],queries=['Unverified legacy lookup'])
        self.save(self.path,self.record)
        self.save(self.root/'bounded-research-policy.json',{**self.policy,'authorization':''})
        self.assertFalse(research.gathering_complete(research.bundle_status(research.read_bundle(self.root))))

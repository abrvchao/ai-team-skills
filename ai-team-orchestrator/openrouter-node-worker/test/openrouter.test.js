import test from 'node:test';
import assert from 'node:assert/strict';
import {allowedModel,queryFree} from '../src/index.js';
test('only free model routes are allowed',()=>{
 assert.equal(allowedModel('openrouter/free'),'openrouter/free');
 assert.equal(allowedModel('nvidia/nemotron-3.5-lightning:free'),'nvidia/nemotron-3.5-lightning:free');
 for(const model of ['openai/gpt-5','openrouter/auto','openrouter/something','evil']) assert.throws(()=>allowedModel(model),/nonfree/);
});
test('paid route denied before any network call',async()=>{
 let calls=0;
 await assert.rejects(queryFree('hello',{key:'synthetic',model:'openai/gpt-5',fetchImpl:()=>{calls++;}}),/nonfree/);
 assert.equal(calls,0);
});
test('free route produces bounded request',async()=>{
 let body;
 const answer=await queryFree('hello',{key:'synthetic',fetchImpl:async(_url,options)=>{body=JSON.parse(options.body);return {ok:true,json:async()=>({choices:[{message:{content:'通过'}}]})};}});
 assert.equal(answer,'通过');assert.equal(body.model,'openrouter/free');assert.equal(body.max_tokens,900);
});

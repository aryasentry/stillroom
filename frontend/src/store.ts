import {create} from 'zustand';
import {api} from './api';
import type {Asset,Project} from './types';
type Mutator=(draft:Project)=>void;
interface State {project:Project|null;saveStatus:'saved'|'saving'|'unsaved'|'error';error:string|null;canUndo:boolean;canRedo:boolean;open:(p:Project)=>void;update:(fn:Mutator)=>void;flush:()=>Promise<void>;undo:()=>void;redo:()=>void;reload:()=>Promise<void>;upload:(file:File,role:Asset['role'],onProgress?:(n:number)=>void,signal?:AbortSignal)=>Promise<Asset>}
let timer:ReturnType<typeof setTimeout>|undefined;
let requests:Promise<void>=Promise.resolve();
let generation=0,session=0;
const past:Project[]=[],future:Project[]=[];
const copy=<T,>(v:T):T=>structuredClone(v);
function schedule(){clearTimeout(timer);timer=setTimeout(()=>{useProjectStore.getState().flush().catch(()=>{})},500)}
function journal(p:Project){try{localStorage.setItem(`stillroom-draft-${p.id}`,JSON.stringify(p))}catch{/* committed server record remains authoritative */}}
function clearJournal(id:string){try{localStorage.removeItem(`stillroom-draft-${id}`)}catch{}}
// Uploads advance the same server revision as saves. Keep the complete
// upload + metadata reload inside the same queue as every metadata save.
function serialize<T>(operation:()=>Promise<T>):Promise<T>{
  const result=requests.then(operation);
  requests=result.then(()=>{},()=>{});
  return result;
}
export const useProjectStore=create<State>((set,get)=>{
  const savePending=async(atSession:number):Promise<void>=>{
    while(session===atSession){
      const current=get().project;if(!current||get().saveStatus==='saved')return;
      const atGeneration=generation;
      set({saveStatus:'saving',error:null});
      try{
        const saved=await api.saveProject(copy(current),current.revision);
        if(session!==atSession||get().project?.id!==current.id)return;
        if(generation===atGeneration){clearJournal(current.id);set({project:saved,saveStatus:'saved',error:null})}
        else{const pending={...get().project!,revision:saved.revision,updatedAt:saved.updatedAt};journal(pending);set({project:pending,saveStatus:'unsaved'})}
      }catch(error){if(session===atSession)set({saveStatus:'error',error:error instanceof Error?error.message:'Could not save. Your edits are still here.'});throw error}
    }
  };
  return {
  project:null,saveStatus:'saved',error:null,canUndo:false,canRedo:false,
  open(project){
    clearTimeout(timer);session++;generation++;past.length=0;future.length=0;
    let restored:Project|null=null;try{const draft=JSON.parse(localStorage.getItem(`stillroom-draft-${project.id}`)||'null');if(draft?.revision===project.revision&&draft?.id===project.id)restored=draft}catch{}
    set({project:restored??project,saveStatus:restored?'unsaved':'saved',error:restored?'Recovered unsaved edits from this browser.':null,canUndo:false,canRedo:false});if(restored)schedule();
  },
  update(mutator){const current=get().project;if(!current)return;const next=copy(current);mutator(next);if(JSON.stringify(next)===JSON.stringify(current))return;past.push(copy(current));if(past.length>60)past.shift();future.length=0;generation++;journal(next);set({project:next,saveStatus:'unsaved',error:null,canUndo:true,canRedo:false});schedule()},
  flush(){clearTimeout(timer);const atSession=session;return serialize(()=>savePending(atSession))},
  undo(){const current=get().project,prior=past.pop();if(!current||!prior)return;future.push(copy(current));const next={...prior,assets:current.assets,revision:current.revision,updatedAt:current.updatedAt};generation++;journal(next);set({project:next,saveStatus:'unsaved',canUndo:past.length>0,canRedo:true,error:null});schedule()},
  redo(){const current=get().project,nextState=future.pop();if(!current||!nextState)return;past.push(copy(current));const next={...nextState,assets:current.assets,revision:current.revision,updatedAt:current.updatedAt};generation++;journal(next);set({project:next,saveStatus:'unsaved',canUndo:true,canRedo:future.length>0,error:null});schedule()},
  reload(){const atSession=session;return serialize(async()=>{await savePending(atSession);if(atSession!==session)return;const id=get().project?.id;if(!id)return;const atGeneration=generation;const remote=await api.getProject(id);if(session===atSession&&get().project?.id===id&&generation===atGeneration)set({project:remote,saveStatus:'saved',error:null})})},
  upload(file,role,onProgress,signal){
    const atSession=session,id=get().project?.id;
    return serialize(async()=>{
      if(!id)throw new Error('Open a room first.');
      if(atSession!==session)throw new Error('The room changed before importing began.');
      await savePending(atSession);
      if(atSession!==session)throw new Error('The room changed before importing began.');
      const beforeUpload=get().project!;
      const asset=await api.uploadAsset(id,file,role,onProgress,signal),remote=await api.getProject(id);
      if(atSession!==session)throw new Error('The room changed while importing. The imported copy is preserved in the original project.');
      const local=get().project!;const pending=get().saveStatus!=='saved';
      if(pending&&remote.revision!==beforeUpload.revision+1){
        const message='This room changed in another window during import. Your edits remain here and the imported file is preserved. Reload before saving.';
        set({saveStatus:'error',error:message});throw new Error(message);
      }
      set({project:{...(pending?local:remote),assets:remote.assets,revision:remote.revision,updatedAt:remote.updatedAt},saveStatus:pending?'unsaved':'saved',error:null});
      if(pending){journal(get().project!);schedule()}else clearJournal(id);
      return asset;
    });
  },
}});
if(typeof window!=='undefined')window.addEventListener('beforeunload',event=>{if(useProjectStore.getState().saveStatus!=='saved'){event.preventDefault();event.returnValue=''}});

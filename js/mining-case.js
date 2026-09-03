const details={
 facts:['REAL-TIME BUSINESS FACTS','Production plans, orders, inventory, rail capacity, routes, port state, equipment, vessel windows, cost, weather and tides.'],
 agents:['SPECIALIST AGENTS','Path planning, resource scheduling, time-window prediction and risk assessment agents operate on the same governed state.'],
 tools:['PROFESSIONAL TOOLS','Operations research, forecasting, constraint solving and multi-scenario simulation are called when the decision requires them.'],
 options:['COMPARABLE OPTIONS','The system presents routes, resource combinations, loading sequences, time windows and cost–risk trade-offs—not one opaque answer.'],
 approve:['HUMAN AUTHORITY','Named business owners confirm the plan. Consequential actions remain subject to permissions, approval and audit.'],
 feedback:['EXECUTION FEEDBACK','Actual outcomes update operating facts, models, constraints and scheduling experience for the next decision cycle.']
};
const detail=document.getElementById('loopDetail');
document.querySelectorAll('#decisionLoop button').forEach(btn=>btn.addEventListener('click',()=>{
 document.querySelectorAll('#decisionLoop button').forEach(b=>b.classList.remove('active'));btn.classList.add('active');
 const d=details[btn.dataset.step];detail.innerHTML=`<span>${d[0]}</span><p>${d[1]}</p>`;
}));
const progress=document.getElementById('progress');
const update=()=>{const h=document.documentElement;const max=h.scrollHeight-innerHeight;progress.style.width=(max?scrollY/max*100:0)+'%'};
addEventListener('scroll',update,{passive:true});update();
const links=[...document.querySelectorAll('.case-header nav a')];
const sections=links.map(a=>document.querySelector(a.getAttribute('href'))).filter(Boolean);
const obs=new IntersectionObserver(entries=>entries.forEach(e=>{if(e.isIntersecting){links.forEach(a=>a.style.color='');const a=links.find(x=>x.getAttribute('href')==='#'+e.target.id);if(a)a.style.color='var(--green)'}}),{rootMargin:'-35% 0px -55% 0px'});sections.forEach(s=>obs.observe(s));
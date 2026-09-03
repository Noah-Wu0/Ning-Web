document.querySelectorAll('[data-scroll]').forEach(link=>{
  link.addEventListener('click',event=>{
    const target=document.querySelector(link.dataset.scroll);
    if(target){event.preventDefault();target.scrollIntoView({behavior:'smooth',block:'start'});}
  });
});

const stepData={
  boss:{
    signal:{status:'Signal detected',title:'A delivery update conflicts with commercial reality',copy:'The project system reports completion. Contract evidence and finance state show that acceptance and invoice conditions are still open.',states:['Project: deployed','Contract: acceptance missing','Finance: invoice ineligible']},
    issue:{status:'Executive issue',title:'The billing window is now a management decision',copy:'Materiality, timing and cross-functional blockage move the issue into the executive attention queue.',states:['Signal','Issue created','Decision required']},
    decision:{status:'Governed decision',title:'Protect value without creating an unapproved commitment',copy:'Evidence, constraints and authority are explicit before the decision is recorded.',states:['Evidence reviewed','Options challenged','Decision recorded']},
    commitment:{status:'Commitment ledger',title:'Named owners confirm evidence and deadlines',copy:'Account, delivery, legal and finance commitments remain versioned until acceptance is verified.',states:['Owner confirmed','Evidence required','Review condition active']},
    outcome:{status:'Outcome verification',title:'Activity becomes acceptance, invoice and cash state',copy:'The issue closes only when authorized evidence changes the relevant enterprise state.',states:['Acceptance signed','Invoice eligible','Outcome reviewed']}
  },
  investor:{
    evidence:{status:'Evidence layer',title:'Source material becomes traceable investment evidence',copy:'Financial, legal, commercial and technical claims retain source anchors, scope, confidence and unresolved conflict.',states:['Source anchored','Claim structured','Conflict visible']},
    diligence:{status:'Specialist diligence',title:'Different analytical roles test the same investment thesis',copy:'Specialized agents apply financial, legal, market, technology, valuation, exit and terms criteria.',states:['Parallel review','Cross-check','Missing evidence']},
    challenge:{status:'IC challenge',title:'The recommendation is challenged before it reaches the committee',copy:'A challenger role tests assumptions, downside exposure and the evidence threshold for proceeding.',states:['Thesis challenged','Range tested','Conditions proposed']},
    memo:{status:'Decision memo',title:'One memo preserves findings, assumptions and conditions',copy:'The investment committee receives an editable, evidence-linked decision instrument rather than a generated summary.',states:['Valuation range','Risk conditions','Exit logic']},
    action:{status:'Transaction action',title:'Risk changes price, terms or the decision',copy:'The system supports human professionals in translating risk into repricing, protective terms, conditional approval or rejection.',states:['Risk classified','Terms proposed','Human decision']}
  },
  sales:{
    snapshot:{status:'CRM snapshot',title:'The recorded opportunity is incomplete',copy:'Amount and stage do not explain whether demand is real, the solution is feasible, or delivery economics will hold.',states:['Amount recorded','Stage recorded','Quality unknown']},
    context:{status:'Living context',title:'Demand, solution, economics and risk become connected',copy:'Ontology connects the customer problem, decision chain, solution boundary, cost, acceptance and payment conditions.',states:['Relationships mapped','Gaps exposed','Context updated']},
    quality:{status:'Quality decision',title:'Resources follow evidence—not deal size alone',copy:'Management can increase investment, observe, validate or stop based on quality and strategic value.',states:['Win evidence','Delivery risk','Investment decision']},
    action:{status:'Next best action',title:'One shared priority coordinates the team',copy:'Sales, solution, legal, delivery and finance work on the highest-leverage unresolved action.',states:['Priority selected','Owner assigned','Due condition']},
    feedback:{status:'Closed loop',title:'Human and agent feedback keeps the opportunity accurate',copy:'Actions, customer response and delivery evidence write back into the shared opportunity context.',states:['Action executed','Feedback captured','Forecast recalibrated']}
  }
};

document.querySelectorAll('.tn-stepper').forEach(stepper=>{
  const product=stepper.dataset.product;
  const panel=stepper.querySelector('.tn-step-panel');
  const status=panel.querySelector('.status');
  const title=panel.querySelector('h3');
  const copy=panel.querySelector('p');
  const state=panel.querySelector('.state-change');
  const render=key=>{
    const data=stepData[product]?.[key];if(!data)return;
    status.textContent=data.status;title.textContent=data.title;copy.textContent=data.copy;
    state.innerHTML=data.states.map((item,index)=>`<span${index===data.states.length-1?' class="current"':''}>${item}</span>`).join('<i>→</i>');
    stepper.querySelectorAll('.tn-step').forEach(button=>button.classList.toggle('on',button.dataset.step===key));
  };
  stepper.querySelectorAll('.tn-step').forEach(button=>button.addEventListener('click',()=>render(button.dataset.step)));
  const first=stepper.querySelector('.tn-step');if(first)render(first.dataset.step);
});

const revealObserver=new IntersectionObserver(entries=>entries.forEach(entry=>{
  if(entry.isIntersecting){entry.target.animate([{opacity:0,transform:'translateY(22px)'},{opacity:1,transform:'translateY(0)'}],{duration:650,easing:'cubic-bezier(.18,.82,.25,1)',fill:'both'});revealObserver.unobserve(entry.target);}
}),{threshold:.08});
document.querySelectorAll('.tn-reveal').forEach(element=>revealObserver.observe(element));

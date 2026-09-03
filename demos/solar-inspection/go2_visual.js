(function(){
  function calibrateFooting(wrapper){
    wrapper.updateMatrixWorld(true);
    var box=new THREE.Box3().setFromObject(wrapper);
    wrapper.userData.footBottom=isFinite(box.min.y)?box.min.y:0;
    wrapper.userData.visualHeight=isFinite(box.max.y-box.min.y)?box.max.y-box.min.y:0.6;
  }

  function createFallback(scene){
    var g=new THREE.Group();
    var bm=new THREE.MeshStandardMaterial({color:0x8a8f96,roughness:.22,metalness:.82});
    function m(geo,c){var o=new THREE.Mesh(geo,c);o.castShadow=o.receiveShadow=true;return o}
    g.add(m(new THREE.BoxGeometry(.52,.18,.24),bm).translateY(.28));
    [[.19,.11],[.19,-.11],[-.19,.11],[-.19,-.11]].forEach(function(p,i){
      var lg=new THREE.Group();lg.position.set(p[0],.08,p[1]);lg.name='leg_'+i;
      lg.add(m(new THREE.CylinderGeometry(.038,.038,.05,12),bm));g.add(lg);
    });
    scene.add(g);calibrateFooting(g);return g;
  }

  function nums(s,fallback){
    if(!s)return fallback||[0,0,0];
    return s.trim().split(/\s+/).map(Number);
  }

  function applyOrigin(obj,originEl){
    var xyz=originEl?nums(originEl.getAttribute('xyz'),[0,0,0]):[0,0,0];
    var rpy=originEl?nums(originEl.getAttribute('rpy'),[0,0,0]):[0,0,0];
    obj.position.set(xyz[0],xyz[1],xyz[2]);
    obj.rotation.set(rpy[0],rpy[1],rpy[2]);
  }

  function resolveMeshPath(modelSpec,filename){
    var f=(filename||'').replace(/^package:\/\/go2_description\//,'').replace(/^\.\//,'');
    return (modelSpec.mesh_base||'/assets/go2/')+f;
  }

  function loadGeometry(modelSpec,callback){
    if(!modelSpec){callback(null);return}
    fetch(modelSpec.urdf_url).then(function(r){
      if(!r.ok)throw new Error('URDF HTTP '+r.status);
      return r.text();
    }).then(function(urdfText){
      var doc=new DOMParser().parseFromString(urdfText,'text/xml');
      var meshFiles={};
      Array.from(doc.querySelectorAll('visual mesh')).forEach(function(meshEl){
        var filename=meshEl.getAttribute('filename');
        if(filename)meshFiles[filename]=resolveMeshPath(modelSpec,filename);
      });
      var loader=new THREE.ColladaLoader();
      var loads=Object.keys(meshFiles).map(function(filename){
        return new Promise(function(resolve){
          loader.load(meshFiles[filename],function(collada){
            collada.scene.traverse(function(n){
              if(n.isMesh){
                n.castShadow=true;n.receiveShadow=true;
                n.material=go2MetalMaterial();
              }
            });
            resolve([filename,collada.scene]);
          },undefined,function(err){
            console.error('DAE load failed:',meshFiles[filename],err);
            resolve([filename,new THREE.Group()]);
          });
        });
      });
      Promise.all(loads).then(function(entries){
        var meshes={};
        entries.forEach(function(e){meshes[e[0]]=e[1]});
        callback({spec:modelSpec,urdfDoc:doc,meshes:meshes});
      });
    }).catch(function(err){
      console.error('Go2 visual URDF failed:',err);
      callback(null);
    });
  }

  function go2MetalMaterial(){
    return new THREE.MeshStandardMaterial({
      color:0x8a8f96,
      roughness:.22,
      metalness:.82,
      side:THREE.DoubleSide
    });
  }

  function applyMetalMaterial(root){
    root.traverse(function(n){
      if(n.isMesh){
        n.material=go2MetalMaterial();
        n.castShadow=true;
        n.receiveShadow=true;
      }
    });
  }

  function createRobot(scene,visual){
    if(!visual)return createFallback(scene);
    var doc=visual.urdfDoc, meshes=visual.meshes;
    var wrapper=new THREE.Group();
    var root=new THREE.Group(),links={},jointNodes={};
    Array.from(doc.querySelectorAll('link')).forEach(function(linkEl){
      var link=new THREE.Group();
      link.name=linkEl.getAttribute('name');
      links[link.name]=link;
      Array.from(linkEl.children).filter(function(c){return c.tagName==='visual'}).forEach(function(visualEl){
        var meshEl=visualEl.querySelector('mesh');
        if(!meshEl)return;
        var filename=meshEl.getAttribute('filename');
        var src=meshes[filename];
        if(!src)return;
        var vis=src.clone(true);
        applyMetalMaterial(vis);
        applyOrigin(vis,visualEl.querySelector('origin'));
        link.add(vis);
      });
    });
    var baseLink=links.base||links[Object.keys(links)[0]];
    if(baseLink)root.add(baseLink);
    Array.from(doc.querySelectorAll('joint')).forEach(function(jointEl){
      var parentEl=jointEl.querySelector('parent'),childEl=jointEl.querySelector('child');
      if(!parentEl||!childEl)return;
      var parent=links[parentEl.getAttribute('link')],child=links[childEl.getAttribute('link')];
      if(!parent||!child)return;
      var joint=new THREE.Group();
      joint.name=jointEl.getAttribute('name');
      joint.userData.jointType=jointEl.getAttribute('type');
      joint.userData.axis=new THREE.Vector3(1,0,0);
      var axisEl=jointEl.querySelector('axis');
      if(axisEl){
        var axis=nums(axisEl.getAttribute('xyz'),[1,0,0]);
        joint.userData.axis.set(axis[0],axis[1],axis[2]).normalize();
      }
      applyOrigin(joint,jointEl.querySelector('origin'));
      joint.userData.basePos=joint.position.clone();
      joint.userData.baseQuat=joint.quaternion.clone();
      joint.add(child);
      parent.add(joint);
      jointNodes[joint.name]=joint;
    });
    root.rotation.x=-Math.PI/2;
    root.setJointValues=function(js){
      Object.keys(js||{}).forEach(function(name){
        var node=jointNodes[name];if(!node)return;
        var value=js[name]||0;
        node.position.copy(node.userData.basePos);
        node.quaternion.copy(node.userData.baseQuat);
        if(node.userData.jointType==='revolute'||node.userData.jointType==='continuous'){
          var q=new THREE.Quaternion().setFromAxisAngle(node.userData.axis,value);
          node.quaternion.multiply(q);
        }else if(node.userData.jointType==='prismatic'){
          node.position.add(node.userData.axis.clone().multiplyScalar(value));
        }
      });
    };
    wrapper.add(root);
    wrapper.userData.visualType='go2_visual_spec';
    wrapper.userData.urdf=root;
    scene.add(wrapper);
    calibrateFooting(wrapper);
    return wrapper;
  }

  function visualY(robotTelemetry,model){
    var gh=window.getGroundHeight(robotTelemetry.pose.x,robotTelemetry.pose.y);
    var foot=model&&model.userData&&isFinite(model.userData.footBottom)?model.userData.footBottom:0;
    return gh-foot+0.02;
  }

  window.GO2Visual={
    loadGeometry:loadGeometry,
    createRobot:createRobot,
    visualY:visualY,
    calibrateFooting:calibrateFooting
  };
})();

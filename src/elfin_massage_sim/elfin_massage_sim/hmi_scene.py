"""Lightweight rotatable Qt scene: mannequin geometry, robot joints and trajectory."""
import numpy as np
from PyQt5.QtCore import Qt,QPointF
from PyQt5.QtGui import QColor,QPainter,QPen,QPolygonF
from PyQt5.QtWidgets import QWidget
from scipy.spatial.transform import Rotation
from .kinematics import origin
from .frames import points,HUMAN_FRAME

class SceneView(QWidget):
    def __init__(self,node):
        super().__init__();self.node=node;self.yaw=.7;self.pitch=.85;self.zoom=260
        self.draft=None;self.preview=None;self.faces=None;self.last_pos=None
        self.setMinimumSize(480,350)
        self.setToolTip('拖动旋转 · 滚轮缩放；青色为已规划轨迹，橙色为实际 TCP')

    def build(self,model):
        tf=model.transforms(np.zeros(6));faces=[];colors=[];layers=[]
        for link in model.root.findall('link'):
            name=link.get('name')
            if not name.startswith(('human_','back_','treatment_','couch_')):continue
            for vis in link.findall('visual'):
                g=vis.find('geometry');color=vis.find('material/color')
                rgb=np.fromstring(color.get('rgba'),sep=' ')[:3] if color is not None else np.array([.6,.65,.7])
                if g.find('box') is not None:
                    size=np.fromstring(g.find('box').get('size'),sep=' ')/2
                    vertices=np.array([[x,y,z] for x in [-1,1] for y in [-1,1] for z in [-1,1]])*size
                    triangles=np.array([[0,1,3],[0,3,2],[4,6,7],[4,7,5],[0,4,5],[0,5,1],[2,3,7],[2,7,6],[0,2,6],[0,6,4],[1,5,7],[1,7,3]])
                    mesh=vertices[triangles]
                else:
                    element=g.find('mesh')
                    if element is None:continue
                    torso=element.get('filename').endswith('torso.stl')
                    scale=np.array([.18,.30,.10]) if torso else np.fromstring(element.get('scale','1 1 1'),sep=' ')
                    rings=[]
                    for lat in np.linspace(-np.pi/2,np.pi/2,9):
                        a=np.linspace(0,2*np.pi,15)
                        r=np.c_[np.cos(lat)*np.cos(a),np.cos(lat)*np.sin(a),np.full(len(a),np.sin(lat))]*scale
                        if torso:r[:,2]=np.minimum(r[:,2],.07)
                        rings.append(r)
                    mesh=np.array([tri for j in range(8) for i in range(14) for tri in
                          [(rings[j][i],rings[j+1][i],rings[j+1][i+1]),(rings[j][i],rings[j+1][i+1],rings[j][i+1])]])
                local=tf[name]@origin(vis.find('origin'));mesh=points(local,mesh)
                normals=np.cross(mesh[:,1]-mesh[:,0],mesh[:,2]-mesh[:,0])
                normals/=np.maximum(np.linalg.norm(normals,axis=1)[:,None],1e-9)
                shade=.70+.30*np.abs(normals@np.array([.3,-.4,.86]))
                faces.extend(mesh);colors.extend(np.clip(rgb[None,:]*shade[:,None]*255,0,255).astype(int))
                layer=2 if name.startswith(('human_','back_')) else (1 if name=='treatment_couch' else 0)
                layers.extend([layer]*len(mesh))
        self.faces=np.asarray(faces);self.colors=colors;self.layers=np.asarray(layers)

    def project(self,p):
        p=np.asarray(p)-[.5,.20,.35]
        r=Rotation.from_euler('z',self.yaw).as_matrix()
        r=Rotation.from_euler('x',-self.pitch).as_matrix()@r
        q=p@r.T
        return np.stack([self.width()/2+q[...,0]*self.zoom,self.height()/2-q[...,1]*self.zoom,q[...,2]],axis=-1)

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(),QColor('#101d29'))
        def line(a,b,color,width=1,style=Qt.SolidLine):
            p=self.project([a,b]);painter.setPen(QPen(QColor(color),width,style));painter.drawLine(QPointF(*p[0,:2]),QPointF(*p[1,:2]))
        for x in np.arange(-.2,1.3,.1):line([x,-.75,0],[x,1.25,0],'#253542')
        for y in np.arange(-.7,1.3,.1):line([-.2,y,0],[1.2,y,0],'#253542')
        model=self.node.registered_display_model or self.node.motion.model
        if model is None:return
        if self.faces is None:self.build(model)
        projected=self.project(self.faces);depth=projected[:,:,2].mean(axis=1)
        painter.setPen(Qt.NoPen)
        for i in np.lexsort((depth,self.layers)):
            color=QColor(*map(int,self.colors[i]));painter.setBrush(color);painter.setPen(QPen(color,.4))
            painter.drawPolygon(QPolygonF([QPointF(float(p[0]),float(p[1])) for p in projected[i]]))
        q=self.node.motion.q
        if q is not None:
            tf=model.transforms(q)
            chain=['elfin_base_link']+[f'elfin_link{i}' for i in range(1,7)]+['massage_tcp']
            vertices=[tf[n][:3,3] for n in chain if n in tf]
            for a,b in zip(vertices,vertices[1:]):line(a,b,'#b3c7d7',9)
            for n in [HUMAN_FRAME,'world']:
                t=tf.get(n,np.eye(4));center=t[:3,3]
                for j,c in enumerate(['#f77575','#80df97','#79baff']):line(center,center+t[:3,j]*.12,c,2)
            target=self.project(tf['massage_tcp'][:3,3]);painter.setPen(Qt.NoPen);painter.setBrush(QColor('#ffb55c'))
            painter.drawEllipse(QPointF(*target[:2]),6,6)
        for path,color,style in [(self.draft,'#94a9b6',Qt.DashLine),(self.preview,'#35e1cd',Qt.SolidLine)]:
            if path is not None:
                for a,b in zip(path,path[1:]):line(a,b,color,3,style)
        actual=[s['world_m'] for s in self.node.actual_trace]
        for a,b in zip(actual,actual[1:]):line(a,b,'#ffad50',2)
        painter.setPen(QColor('#b8cfdf'));painter.drawText(16,25,'三维预览 · '+('视觉配准人体' if self.node.registration is not None else '未配准 · 人体仅为场景参考')+' · 拖动旋转 / 滚轮缩放')
        painter.drawText(16,self.height()-16,'虚线：编辑路径    青色：已规划    橙色：实际 TCP    XYZ：红 / 绿 / 蓝')

    def mousePressEvent(self,event):self.last_pos=event.pos()
    def mouseMoveEvent(self,event):
        if self.last_pos is not None:
            delta=event.pos()-self.last_pos;self.yaw+=delta.x()*.007;self.pitch=np.clip(self.pitch+delta.y()*.007,.1,1.5)
            self.last_pos=event.pos();self.update()
    def mouseReleaseEvent(self,event):self.last_pos=None
    def wheelEvent(self,event):self.zoom=np.clip(self.zoom*(1.1 if event.angleDelta().y()>0 else .9),140,550);self.update()

"""Chinese Qt supervisory UI. Planning is read-only; execution is a separate action."""
import json
import time
from pathlib import Path
import numpy as np
import cv2
import rclpy
from rclpy.time import Time
from PyQt5.QtCore import Qt,QTimer,QRectF,pyqtSignal
from PyQt5.QtGui import QPainter,QImage,QColor,QFont,QKeySequence
from PyQt5.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,
 QGroupBox,QLabel,QComboBox,QDoubleSpinBox,QPushButton,QTableWidget,QTableWidgetItem,
 QHeaderView,QTabWidget,QPlainTextEdit,QFileDialog,QSplitter,QShortcut,QScrollArea)
from .hmi_backend import HmiNode
from .registration import VISION_FRAME
from .hmi_scene import SceneView
from .frames import template_path,validate_body_path,points,matrix,inverse,HUMAN_FRAME

PHASES={'WAITING':'等待连接','EDITING':'编辑轨迹','PLANNING':'正在规划与检查碰撞','PREVIEW_READY':'预览就绪 · 等待执行',
 'OBSERVE':'返回观察位','PREAPPROACH':'移动到接近点','NORMAL_APPROACH':'沿法向接近','TRACE':'正在循迹',
 'RETREAT':'沿法向退回','COMPLETE':'执行完成','CANCELLING':'正在停止','CANCELLED':'已停止'}

class CameraView(QWidget):
    picked=pyqtSignal(str,int,int,object)
    def __init__(self,source):
        super().__init__();self.source=source;self.image=None;self.snapshot=None;self.rect_image=QRectF()
        self.setMinimumSize(280,180);self.setMouseTracking(True)
    def set_frame(self,node,snapshot):
        if snapshot is None:return
        self.snapshot=snapshot
        rgb=cv2.cvtColor(node.bridge.imgmsg_to_cv2(snapshot[0],'bgr8'),cv2.COLOR_BGR2RGB)
        self.image=QImage(rgb.data,rgb.shape[1],rgb.shape[0],rgb.strides[0],QImage.Format_RGB888).copy()
        self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#101d29'))
        if self.image is None:
            p.setPen(QColor('#92a9ba'));p.drawText(self.rect(),Qt.AlignCenter,'等待 RGB-D 图像');return
        size=self.image.size();scale=min(self.width()/size.width(),self.height()/size.height())
        w,h=size.width()*scale,size.height()*scale
        self.rect_image=QRectF((self.width()-w)/2,(self.height()-h)/2,w,h)
        p.drawImage(self.rect_image,self.image)
    def mousePressEvent(self,event):
        if event.button()!=Qt.LeftButton or self.image is None or not self.rect_image.contains(event.pos()):return
        r=self.rect_image
        u=int((event.x()-r.x())/r.width()*self.image.width());v=int((event.y()-r.y())/r.height()*self.image.height())
        self.picked.emit(self.source,u,v,self.snapshot)

class HmiWindow(QMainWindow):
    def __init__(self,node):
        super().__init__();self.node=node;self.custom=[];self.body_path=None;self.last_mapping=None
        self.mapping_audit=[]
        self.table_guard=False;self.last_frame_update=0.;self.last_events=0
        self.setWindowTitle('ELFIN 康复机器人 · 轨迹工作站');self.resize(1480,940)
        self.setStyleSheet('''QWidget{background:#101923;color:#dce8f3;font-size:13px;font-family:"Noto Sans CJK SC";}
 QGroupBox{border:1px solid #304253;border-radius:8px;margin-top:12px;padding:12px 8px 6px;font-weight:600;}
 QGroupBox::title{subcontrol-origin:margin;left:12px;padding:0 5px;color:#7eddd3;}
 QComboBox,QDoubleSpinBox,QPlainTextEdit,QTableWidget{background:#192735;border:1px solid #35495c;border-radius:4px;padding:5px;}
 QPushButton{background:#253b4d;border:1px solid #3a556d;border-radius:5px;padding:10px;font-weight:600;}
 QPushButton:hover{background:#35546c;} QPushButton:disabled{color:#637689;background:#182532;}
 QPushButton#execute{background:#147c70;} QPushButton#stop{background:#7c3540;}
 QHeaderView::section{background:#263b4c;color:#b6ccdc;padding:5px;border:0;}
 QTabBar::tab{padding:9px 13px;background:#172634;}QTabBar::tab:selected{background:#2c4557;color:#72e4d5;}
 QSplitter::handle{background:#263748;}''')
        root=QWidget();self.setCentralWidget(root);layout=QVBoxLayout(root);layout.setContentsMargins(16,12,16,12)
        header=QHBoxLayout();title=QLabel('ELFIN  /  康复机器人轨迹工作站');title.setStyleSheet('font-size:23px;font-weight:600;')
        header.addWidget(title);header.addStretch();self.connection=QLabel('连接中');header.addWidget(self.connection);layout.addLayout(header)
        self.phase=QLabel('① 选择人体    →    ② 编辑路径    →    ③ 规划预览    →    ④ 执行循迹')
        self.phase.setStyleSheet('padding:10px;background:#18322f;color:#7de0cb;border-radius:5px;');layout.addWidget(self.phase)
        splitter=QSplitter(Qt.Horizontal);layout.addWidget(splitter,1)
        left=QWidget();lv=QVBoxLayout(left);lv.setContentsMargins(0,0,5,0)
        select=QGroupBox('01  人体与区域');form=QVBoxLayout(select)
        self.human=QComboBox();self.human.addItems(['请选择场景中的人体','人体 01 · 俯卧模型']);form.addWidget(self.human)
        self.region=QComboBox();self.region.addItems(['背部中央','背部左侧','背部右侧']);form.addWidget(self.region)
        label=QLabel('坐标基准：human_estimated\n三色标记 RGB-D 配准 · 原点在背部中央\nX：横向  Y：纵向  Z：表面外法向');label.setWordWrap(True);form.addWidget(label)
        self.registration_source=QComboBox();self.registration_source.addItem('末端 RGB-D','wrist')
        if node.sensing_mode=='dual':self.registration_source.addItem('全局 RGB-D','global')
        form.addWidget(self.registration_source)
        self.register_button=QPushButton('采集并配准人体');self.register_button.clicked.connect(self.register_body);form.addWidget(self.register_button)
        self.registration_status=QLabel('未配准 · 请先返回观察位');self.registration_status.setWordWrap(True);form.addWidget(self.registration_status);lv.addWidget(select)
        config=QGroupBox('02  轨迹设置');f=QVBoxLayout(config)
        self.kind=QComboBox();self.kind.addItems(['纵向直线','横向往返','椭圆轨迹','相机点选折线']);f.addWidget(self.kind)
        f.addWidget(QLabel('循迹速度'));self.speed=QDoubleSpinBox();self.speed.setRange(5,40);self.speed.setValue(20);self.speed.setSuffix(' mm/s');f.addWidget(self.speed)
        f.addWidget(QLabel('表面间隙'));self.clearance=QDoubleSpinBox();self.clearance.setRange(20,80);self.clearance.setValue(20);self.clearance.setSuffix(' mm');f.addWidget(self.clearance)
        hint=QLabel('相机点选：在任一路画面中依次点击背部。表格可编辑人体坐标。');hint.setWordWrap(True);f.addWidget(hint)
        self.clear=QPushButton('清空点选');self.clear.clicked.connect(self.clear_points);f.addWidget(self.clear);lv.addWidget(config)
        actions=QGroupBox('03  规划与执行');a=QVBoxLayout(actions)
        self.plan_button=QPushButton('规划并预览');self.plan_button.clicked.connect(self.plan);a.addWidget(self.plan_button)
        self.execute_button=QPushButton('执行已选轨迹');self.execute_button.setObjectName('execute');self.execute_button.clicked.connect(self.execute);a.addWidget(self.execute_button)
        self.stop_button=QPushButton('停止运动  [空格]');self.stop_button.setObjectName('stop');self.stop_button.clicked.connect(node.motion.cancel);a.addWidget(self.stop_button)
        self.view_button=QPushButton('返回观察位');self.view_button.clicked.connect(self.observe);a.addWidget(self.view_button)
        self.summary=QLabel('尚未规划');self.summary.setWordWrap(True);a.addWidget(self.summary);lv.addWidget(actions)
        row=QHBoxLayout();save=QPushButton('保存方案');save.clicked.connect(self.save);load=QPushButton('载入方案');load.clicked.connect(self.load);row.addWidget(save);row.addWidget(load);lv.addLayout(row)
        warning=QLabel('人体静止 · 位姿采集后锁定\n移动人体后必须重新配准\n仿真保留间隙，尚无接触力控');warning.setStyleSheet('color:#d8b889;');lv.addWidget(warning);lv.addStretch()
        self.settings=[self.registration_source,self.human,self.region,self.kind,self.speed,self.clearance,self.clear,load]
        left.setMinimumWidth(260)
        left_scroll=QScrollArea();left_scroll.setWidgetResizable(True);left_scroll.setWidget(left)
        left_scroll.setMinimumWidth(280);left_scroll.setMaximumWidth(320);splitter.addWidget(left_scroll)
        center=QWidget();cv=QVBoxLayout(center);cv.setContentsMargins(4,0,4,0)
        self.scene=SceneView(node);cv.addWidget(self.scene,3)
        self.table=QTableWidget(0,7);self.table.setHorizontalHeaderLabels(['点','H.x mm','H.y mm','H.z mm','W.x mm','W.y mm','W.z mm'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);self.table.verticalHeader().hide();self.table.cellChanged.connect(self.edit_cell)
        self.table.setMinimumHeight(170);cv.addWidget(self.table,1)
        self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMaximumHeight(115);cv.addWidget(self.log);splitter.addWidget(center)
        right=QWidget();rv=QVBoxLayout(right);rv.setContentsMargins(4,0,0,0)
        self.tabs=QTabWidget();videos=QWidget();vv=QVBoxLayout(videos);vv.setContentsMargins(0,0,0,0)
        self.global_label=QLabel('全局 RGB-D  ·  固定相机');vv.addWidget(self.global_label);self.global_view=CameraView('global');vv.addWidget(self.global_view,1)
        if node.sensing_mode=='wrist':self.global_label.hide();self.global_view.hide()
        vv.addWidget(QLabel('末端 RGB-D  ·  随工具运动'));self.wrist_view=CameraView('wrist');vv.addWidget(self.wrist_view,1)
        self.tabs.addTab(videos,'双相机' if node.sensing_mode=='dual' else '仅末端相机');self.frames_text=QPlainTextEdit();self.frames_text.setReadOnly(True);self.tabs.addTab(self.frames_text,'坐标变换');rv.addWidget(self.tabs,3)
        distance=QGroupBox('实时测距 · 相机光心到人体');dv=QVBoxLayout(distance)
        self.distance_label=QLabel('先配准人体');self.distance_label.setWordWrap(True);dv.addWidget(self.distance_label)
        distance.setToolTip('原点距离：曝光时刻相机位置到已配准人体原点的直线距离。\n背部距离：位于已配准中央背部范围内的有效深度点到相机的距离中位数。\n不是全身最小间距；人体移动后需重新配准。')
        rv.addWidget(distance)
        mapping=QGroupBox('像素映射结果');mv=QVBoxLayout(mapping);self.mapping=QLabel('在相机中点击背部查看\n像素 → 相机 → 世界 → 人体');self.mapping.setWordWrap(True);mv.addWidget(self.mapping);rv.addWidget(mapping)
        right.setMinimumWidth(320);right.setMaximumWidth(400);splitter.addWidget(right);splitter.setSizes([285,790,355])
        for view in [self.global_view,self.wrist_view]:view.picked.connect(self.pick)
        for widget in [self.human,self.region,self.kind]:widget.currentIndexChanged.connect(self.changed)
        self.speed.valueChanged.connect(self.changed);self.clearance.valueChanged.connect(self.changed)
        self.statusBar().showMessage('轨迹基准为人体坐标；已规划方案在参数或配准改变后失效')
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(100)
        self.ros_timer=QTimer(self);self.ros_timer.timeout.connect(self.spin);self.ros_timer.start(10)
        self.stop_shortcut=QShortcut(QKeySequence(Qt.Key_Space),self)
        self.stop_shortcut.setContext(Qt.ApplicationShortcut);self.stop_shortcut.activated.connect(node.motion.cancel)
        self.changed()

    def spin(self):
        if not rclpy.ok():QApplication.quit();return
        deadline=time.monotonic()+.010
        for _ in range(120):
            rclpy.spin_once(self.node,timeout_sec=0)
            if time.monotonic()>deadline:break

    def changed(self,*args):
        self.node.invalidate();self.scene.preview=None;self.scene.draft=None;self.summary.setText('设置已变更，请重新规划')
        if self.human.currentIndex()==0:self.body_path=None
        elif self.kind.currentText()=='相机点选折线':
            self.region.blockSignals(True);self.region.setCurrentIndex(0);self.region.blockSignals(False)
            self.body_path=np.asarray(self.custom) if self.custom else None
        else:self.body_path=template_path(self.kind.currentText(),self.region.currentText())
        self.fill_table();self.update_draft()

    def update_draft(self):
        if self.body_path is None:return
        try:self.scene.draft=points(self.node.body_pose(),self.body_path+[0,0,self.clearance.value()/1000])
        except Exception:self.scene.draft=None

    def fill_table(self):
        self.table_guard=True;self.table.setRowCount(0 if self.body_path is None else len(self.body_path))
        if self.body_path is not None:
            try:world=points(self.node.body_pose(),self.body_path)
            except Exception:world=np.full_like(self.body_path,np.nan)
            for i,p in enumerate(self.body_path):
                values=[str(i+1)]+[f'{v*1000:.2f}' for v in p]+[f'{v*1000:.2f}' if np.isfinite(v) else '—' for v in world[i]]
                for j,value in enumerate(values):
                    item=QTableWidgetItem(value)
                    if j not in (1,2,3):item.setFlags(item.flags()&~Qt.ItemIsEditable)
                    self.table.setItem(i,j,item)
        self.table_guard=False

    def edit_cell(self,row,col):
        if self.table_guard or self.node.motion.busy:return
        try:
            p=np.array([[float(self.table.item(i,j).text())/1000 for j in (1,2,3)] for i in range(self.table.rowCount())])
            validate_body_path(p)
            self.custom=p.tolist();self.mapping_audit=[];self.kind.blockSignals(True);self.kind.setCurrentText('相机点选折线');self.kind.blockSignals(False);self.changed()
        except Exception as exc:self.node.notice(str(exc));self.fill_table()

    def clear_points(self):self.custom=[];self.mapping_audit=[];self.changed()
    def pick(self,source,u,v,snapshot):
        if self.node.motion.busy:self.node.notice('正在规划或执行，先停止后再编辑路径');return
        if self.human.currentIndex()==0:self.node.notice('请先选择人体');return
        try:
            mapped=self.node.pick(source,u,v,snapshot);p=mapped['human_m']
            if self.custom and np.linalg.norm(np.asarray(p)-self.custom[-1])<.002:raise ValueError('相邻点过近，请选择另一个位置')
            self.last_mapping=mapped;self.custom.append(p);self.mapping_audit.append(mapped)
            self.kind.blockSignals(True);self.kind.setCurrentText('相机点选折线');self.kind.blockSignals(False);self.changed()
            self.mapping.setText('像素  (%d, %d)  ·  %s\n相机  %s\n世界  %s\n人体  %s\n单位 m · 图像时间戳 %.3f s'%
                (u,v,source,tuple(round(x,4) for x in mapped['camera_m']),tuple(round(x,4) for x in mapped['world_m']),
                 tuple(round(x,4) for x in p),mapped['stamp_ns']/1e9))
        except Exception as exc:self.node.notice(str(exc))

    def plan(self):
        try:
            if self.human.currentIndex()==0:raise ValueError('请先选择场景中的人体')
            if self.body_path is None:raise ValueError('请设置轨迹或在相机中点选至少两个点')
            if self.node.prepare(self.body_path,self.clearance.value()/1000,self.speed.value()/1000):
                self.node.spec['camera_measurements']=self.mapping_audit if self.kind.currentText()=='相机点选折线' else []
        except Exception as exc:self.node.notice(str(exc))
    def execute(self):
        try:
            if not self.node.execute():self.node.notice('方案未就绪或起始状态已改变，请重新规划')
        except Exception as exc:self.node.notice(str(exc))
    def observe(self):
        self.node.invalidate();self.scene.preview=None;self.node.motion.start('observe')

    def register_body(self):
        try:
            self.node.register_body(self.registration_source.currentData())
            self.custom=[];self.mapping_audit=[];self.last_mapping=None
            self.mapping.setText('配准已更新，请重新选择轨迹点')
            self.changed()
        except Exception as exc:self.node.notice('视觉配准失败：'+str(exc));self.changed()
        self.scene.faces=None

    def save(self):
        if self.body_path is None:return
        filename,_=QFileDialog.getSaveFileName(self,'保存人体坐标轨迹','/workspace/plans/trajectory.json','JSON (*.json)')
        if not filename:return
        try:
            data={'version':2,'human_id':'human_01','frame_id':VISION_FRAME,'body_path_m':validate_body_path(self.body_path).tolist(),
                  'visual_registration':self.node.registration,
                  'clearance_m':self.clearance.value()/1000,'speed_m_s':self.speed.value()/1000,
                  'camera_measurements':self.mapping_audit if self.kind.currentText()=='相机点选折线' else []}
            Path(filename).parent.mkdir(parents=True,exist_ok=True);Path(filename).write_text(json.dumps(data,indent=2));self.node.notice('人体坐标方案已保存；载入后需重新规划')
        except Exception as exc:self.node.notice(str(exc))
    def load(self):
        filename,_=QFileDialog.getOpenFileName(self,'载入人体坐标轨迹','/workspace/plans','JSON (*.json)')
        if not filename:return
        try:
            data=json.loads(Path(filename).read_text())
            if data.get('version')!=2 or data.get('human_id')!='human_01' or data.get('frame_id')!=VISION_FRAME:raise ValueError('方案版本、人体标识或坐标系不匹配')
            p=validate_body_path(data['body_path_m']);c=float(data['clearance_m']);s=float(data['speed_m_s'])
            if not .02<=c<=.08 or not .005<=s<=.04:raise ValueError('速度或间隙超出范围')
            self.custom=p.tolist();self.mapping_audit=[];self.human.setCurrentIndex(1);self.kind.setCurrentText('相机点选折线');self.clearance.setValue(c*1000);self.speed.setValue(s*1000);self.changed()
        except Exception as exc:self.node.notice(str(exc))

    def refresh(self):
        motion=self.node.motion;busy=motion.busy
        self.connection.setText('● 控制器已连接' if motion.ready() else '○ 等待反馈 / 检查仿真播放')
        if motion.model is not None and motion.q is not None:
            try:
                w=motion.model.fk(motion.q)[:3,3];h=points(inverse(self.node.body_pose()),w)
                self.statusBar().showMessage('TCP 世界 (%.1f, %.1f, %.1f) mm   |   人体 (%.1f, %.1f, %.1f) mm'%tuple(np.r_[w,h]*1000))
            except Exception:pass
        self.phase.setText(PHASES.get(motion.phase,motion.phase))
        for w in self.settings:w.setEnabled(not busy)
        self.region.setEnabled(not busy and self.kind.currentText()!='相机点选折线')
        registered=self.node.registration is not None
        self.register_button.setEnabled(not busy and motion.ready())
        if registered:
            reg=self.node.registration;comparison=reg['reference_comparison'];xyz=np.asarray(reg['T_world_human'])[:3,3]*1000
            self.registration_status.setText('视觉配准 #%d · %s\n估计原点 (%.1f, %.1f, %.1f) mm\n拟合 RMS %.2f mm\n预设参考（仅评估）\n平移误差 %.2f mm · 角度误差 %.2f°'%
                (reg['revision'],'末端' if reg['source']=='wrist' else '全局',*xyz,reg['fit_rms_mm'],comparison['translation_error_mm'],comparison['rotation_error_deg']))
        else:
            self.registration_status.setText('未配准 · 请先返回观察位\n三色标记须全部可见')
            if not busy:self.phase.setText('① 返回观察位 → ② 采集并配准人体 → ③ 选择轨迹 → ④ 规划预览 → ⑤ 执行')
        self.registration_status.setMinimumHeight(max(self.registration_status.heightForWidth(self.registration_status.width()),
            (self.registration_status.text().count('\n')+1)*self.registration_status.fontMetrics().lineSpacing()+12))
        self.table.setEnabled(not busy);self.plan_button.setEnabled(not busy and registered and self.human.currentIndex()==1)
        self.view_button.setEnabled(not busy);self.execute_button.setEnabled(not busy and motion.prepared is not None and motion.ready())
        self.global_view.set_frame(self.node,self.node.display_frame('global'))
        self.wrist_view.set_frame(self.node,self.node.display_frame('wrist'))
        ranges=[]
        for source,result in self.node.range_status.items():
            name='末端相机' if source=='wrist' else '全局相机'
            if not result['valid']:ranges.append(name+'：— '+result['reason']);continue
            surface=('%.1f mm'% (result['surface_range_m']*1000)) if result['surface_valid'] else '— 背部深度不足 / 遮挡'
            ranges.append('%s · 帧龄 %.0f ms\n人体原点 %.1f mm（坐标计算）\n可见背部 %s（深度中位数）'%
                (name,result['age_s']*1000,result['origin_distance_m']*1000,surface))
        self.distance_label.setText('\n\n'.join(ranges) if ranges else '等待测距')
        self.distance_label.setMinimumHeight(max(self.distance_label.heightForWidth(self.distance_label.width()),
            (self.distance_label.text().count('\n')+1)*self.distance_label.fontMetrics().lineSpacing()+12))
        if motion.prepared is not None:
            self.scene.preview=self.node.preview()
            self.summary.setText('碰撞与限位检查通过\n%d 个阶段 · 预计 %.1f s\n点击执行后按此预览循迹'%(len(motion.prepared),sum(s.times[-1] for s in motion.prepared)))
        elif motion.phase=='PLANNING':self.summary.setText('正在计算逆解与碰撞检查…\n预览就绪前不会执行运动')
        if self.body_path is not None and self.scene.draft is None:self.update_draft();self.fill_table()
        self.scene.update()
        if self.node.events and self.node.events[-1]!=self.last_events:
            self.last_events=self.node.events[-1];self.log.setPlainText('\n'.join(t+'  '+text for t,text in self.node.events));self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
        if time.monotonic()-self.last_frame_update>1.:
            self.last_frame_update=time.monotonic();blocks=['T_target_source：将 source 中的点变换到 target\n单位：米；光学坐标 +Z 向前，+X 向右，+Y 向下']
            names=([VISION_FRAME] if registered else [])+[HUMAN_FRAME,'camera_optical_frame']
            if self.node.sensing_mode=='dual':names.append('global_camera_optical_frame')
            blocks.append('human_estimated：视觉估计，规划使用\nhuman_frame：仿真真值，仅误差评估\n估计位姿采集后锁定，不代表持续跟踪')
            for name in names:
                try:
                    t=matrix(self.node.tf.lookup_transform('world',name,Time()))
                    blocks.append('T_world_'+name+'\n'+np.array2string(t,precision=4,suppress_small=True))
                except Exception:blocks.append(name+'：等待 TF')
            blocks.append('p_world = T_world_human · p_human\np_human = inverse(T_world_human) · T_world_camera(t) · p_camera\n末端相机使用图像曝光时刻 t 的 TF。')
            self.frames_text.setPlainText('\n\n'.join(blocks))

    def keyPressEvent(self,event):
        if event.key()==Qt.Key_Space:self.node.motion.cancel()
        else:super().keyPressEvent(event)
    def closeEvent(self,event):
        if self.node.motion.busy:
            self.node.motion.cancel();self.node.notice('正在停止，完成后可关闭窗口');event.ignore()
        else:event.accept()


def main(args=None):
    rclpy.init(args=args);app=QApplication([]);app.setFont(QFont('Noto Sans CJK SC',10))
    node=HmiNode();window=HmiWindow(node);window.show()
    try:app.exec_()
    finally:
        node.motion.close();node.destroy_node()
        if rclpy.ok():rclpy.shutdown()

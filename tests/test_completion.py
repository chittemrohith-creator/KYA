import io
from datetime import date, timedelta
import pytest
from app import create_app
from app.models import db, User, Project, CitizenPost, Department, JointSchedule, ProjectPhoto, AuditLog, Subscription, utcnow
from app import services
from app.services import BusinessRuleError


def project_pair():
    return Project.query.order_by(Project.id).all()[:2]


def test_memory_bootstrap():
    app = create_app({'TESTING': True, 'TESTING_SEED_MINIMAL': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:'})
    with app.app_context():
        assert db.engine.url.database == ':memory:'
        assert Department.query.count() == 6
        db.session.remove()
        db.engine.dispose()


@pytest.mark.parametrize('lat,lon', [('nan','1'),('inf','1'),('91','1'),('1','181'),('1',''),('', '1'),('x','y')])
def test_invalid_coordinates(citizen_client, app, lat, lon):
    res = citizen_client.post('/dashboard/reports', data={'body':'Safety issue','address':'Demo landmark','latitude':lat,'longitude':lon})
    assert res.status_code == 200
    with app.app_context():
        assert CitizenPost.query.filter_by(body='Safety issue').count() == 0


def test_address_only_and_map(citizen_client, client, app):
    assert citizen_client.post('/dashboard/reports', data={'body':'Address only','address':'Demo street'}).status_code == 302
    with app.app_context():
        cp = CitizenPost.query.filter_by(body='Address only').one()
        assert cp.latitude is None and cp.longitude is None
        cid = cp.id
    assert not any(p['kind']=='citizen_report' and p['id']==cid for p in client.get('/api/mapdata').json['pins'])
    assert citizen_client.post('/dashboard/reports', data={'body':'At zero','latitude':'0','longitude':'0'}).status_code == 302
    with app.app_context():
        cp = CitizenPost.query.filter_by(body='At zero').one()
        assert cp.latitude == 0 and cp.longitude == 0


@pytest.mark.parametrize('data', [{'body':'' ,'address':'Demo'}, {'body':'No location'}])
def test_report_required_fields(citizen_client, app, data):
    before = citizen_client.get('/api/citizen-reports').json['reports']
    assert citizen_client.post('/dashboard/reports', data=data).status_code == 200
    assert len(citizen_client.get('/api/citizen-reports').json['reports']) == len(before)


def test_edit_limits_flags_privacy(app, client):
    with app.app_context():
        cp = CitizenPost.query.first(); owner=cp.user
        services.edit_citizen_post(cp, owner, 'Updated')
        cp.created_at = utcnow() - timedelta(hours=25)
        with pytest.raises(BusinessRuleError): services.edit_citizen_post(cp, owner, 'Too late')
        with pytest.raises(BusinessRuleError): services.delete_citizen_post(cp, owner)
        with pytest.raises(BusinessRuleError): services.flag_citizen_post(cp, owner)
        for i in range(5):
            u=User(role='citizen', status='active', phone_verified=True, display_name='Tester')
            db.session.add(u);db.session.flush()
            services.flag_citizen_post(cp,u);services.flag_citizen_post(cp,u)
        assert cp.flag_count == 5 and cp.status == 'under_review'
        db.session.commit()
    payload=client.get('/api/citizen-reports').json
    assert 'phone' not in str(payload) and 'employee_code' not in str(payload)


def test_subscription_ownership(citizen_client, app):
    citizen_client.post('/dashboard/subscriptions',data={'ward':'Demo Ward','category':'road'})
    citizen_client.post('/dashboard/subscriptions',data={'ward':'Demo Ward','category':'road'})
    with app.app_context():
        assert Subscription.query.filter_by(ward='Demo Ward').count()==1
        other=User(role='citizen',status='active');db.session.add(other);db.session.flush()
        sub=Subscription(user_id=other.id,ward='Other');db.session.add(sub);db.session.commit();sid=sub.id
    assert citizen_client.post('/dashboard/subscriptions/'+str(sid)+'/delete').status_code == 403


def test_joint_proposal_and_approval(app):
    with app.app_context():
        a,b=project_pair(); employee=a.creator; chairman=User.query.filter_by(role='chairman').one()
        js=services.propose_joint_schedule(employee,[a.id,b.id],'Demo stretch',date.today(),date.today()+timedelta(days=1),'Shared trench')
        services.approve_joint_schedule(js,chairman)
        assert js.status=='approved'
        with pytest.raises(BusinessRuleError):services.reject_joint_schedule(js,chairman,'No')
        with pytest.raises(BusinessRuleError):services.propose_joint_schedule(employee,[a.id,b.id],'Demo',date.today(),date.today()-timedelta(days=1),'Reason')
        b.longitude += 1
        with pytest.raises(BusinessRuleError):services.propose_joint_schedule(employee,[a.id,b.id],'Demo',date.today(),date.today(),'Reason')


def test_joint_all_pairs_and_active(app):
    with app.app_context():
        a,b=project_pair();a.latitude=b.latitude=0;a.longitude=0;b.longitude=.0015
        c=Project(title='Third',department_id=b.department_id,category='road',start_date=date.today(),end_date=date.today(),status='approved',latitude=0,longitude=.003,created_by=b.created_by)
        db.session.add(c);db.session.flush()
        with pytest.raises(BusinessRuleError):services.propose_joint_schedule(a.creator,[a.id,b.id,c.id],'Demo',date.today(),date.today(),'Reason')
        a.creator.status='suspended'
        with pytest.raises(BusinessRuleError):services.propose_joint_schedule(a.creator,[a.id,b.id],'Demo',date.today(),date.today(),'Reason')


def test_delay_sweep(app):
    with app.app_context():
        p=project_pair()[0];p.status='in_progress';p.end_date=utcnow().date()-timedelta(days=5)
        services.run_delay_sweep();assert p.status=='delayed' and p.unexplained_delay
        with pytest.raises(BusinessRuleError):services.post_delay_reason(p,p.creator,'')
        services.post_delay_reason(p,p.creator,'Rain');assert not p.unexplained_delay


def test_department_crud_and_references(admin_client, app, roads_client):
    assert roads_client.post('/admin/departments',data={'name':'Unauthorized','slug':'unauthorized'}).status_code==302
    admin_client.post('/admin/departments',data={'name':'Demo New','slug':'demo-new'})
    with app.app_context():did=Department.query.filter_by(slug='demo-new').one().id
    admin_client.post('/admin/departments',data={'action':'edit','department_id':did,'name':'Demo Edited','slug':'demo-edited'})
    admin_client.post('/admin/departments',data={'action':'delete','department_id':did})
    with app.app_context():
        assert db.session.get(Department,did) is None
        assert AuditLog.query.filter_by(action='department.deleted',target_id=did).count()==1
        roads=Department.query.filter_by(slug='roads').one().id
    res=admin_client.post('/admin/departments',data={'action':'delete','department_id':roads})
    assert b'cannot be deleted' in res.data


def test_chairman_project_workflow(chairman_client, roads_client, app):
    with app.app_context():a,b=project_pair();pid=b.id;aid=a.id
    assert chairman_client.post(f'/chairman/projects/{pid}/approve').status_code==302
    assert roads_client.post(f'/workspace/projects/{aid}',data={'action':'start'}).status_code==302
    with app.app_context():assert db.session.get(Project,pid).status=='approved';assert db.session.get(Project,aid).status=='in_progress'


def test_rejection_reason(chairman_client, app):
    with app.app_context():pid=project_pair()[1].id
    assert chairman_client.post(f'/chairman/projects/{pid}/reject',data={'reason':''}).status_code==400
    assert chairman_client.post(f'/chairman/projects/{pid}/reject',data={'reason':'Schedule conflict'}).status_code==302
    with app.app_context():assert db.session.get(Project,pid).status=='rejected'


def test_upload_and_completion(roads_client, chairman_client, client, app, tmp_path):
    from PIL import Image
    app.config['UPLOAD_FOLDER']=str(tmp_path/'uploads')
    with app.app_context():p=project_pair()[0];p.status='in_progress';p.progress_percentage=100;db.session.commit();pid=p.id
    # Arbitrary URLs are not proof.
    roads_client.post(f'/workspace/projects/{pid}',data={'action':'complete','note':'Done','photo_url':'https://invalid.test/fake.jpg'})
    with app.app_context():assert db.session.get(Project,pid).status=='in_progress'
    for content in (b'<svg onload="alert(1)"></svg>', b'not an image'):
        roads_client.post(f'/workspace/projects/{pid}',data={'action':'complete','note':'Done','photo':(io.BytesIO(content),'proof.png')})
        with app.app_context():assert ProjectPhoto.query.filter_by(project_id=pid).count()==0
    image=io.BytesIO();Image.new('RGB',(16,16),'red').save(image,'PNG');image.seek(0)
    assert roads_client.post(f'/workspace/projects/{pid}',data={'action':'complete','note':'Verified work finished','photo':(image,'../../evil.html')}).status_code==302
    with app.app_context():
        assert db.session.get(Project,pid).status=='completed';photo=ProjectPhoto.query.filter_by(project_id=pid).one();url=photo.photo_url
        assert '..' not in url and url.endswith('.jpg')
    assert client.get(url).status_code==200
    assert client.get(url).headers['X-Content-Type-Options']=='nosniff'
    assert chairman_client.post(f'/chairman/projects/{pid}/verify').status_code==302


def test_templates_and_literal_targets(app):
    with app.app_context():
        for name in app.jinja_env.list_templates():app.jinja_env.get_template(name)
        assert 'employee.propose_joint' in app.view_functions
        assert 'admin.departments' in app.view_functions


def test_suspended_api(app, roads_client):
    with app.app_context():u=User.query.filter_by(employee_code='RD-1001').one();u.status='suspended';db.session.commit()
    assert roads_client.get('/api/notifications').status_code==403


@pytest.mark.parametrize('action', ['complete','progress','dates'])
def test_other_department_denied(roads_client, app, action):
    with app.app_context():pid=project_pair()[1].id
    assert roads_client.post(f'/workspace/projects/{pid}',data={'action':action}).status_code==403


@pytest.mark.parametrize('data', [{'title':'Bad dates','latitude':'12','longitude':'77','start_date':'2026-10-20','end_date':'2026-10-19'}, {'title':'Invalid point','latitude':'nan','longitude':'77','start_date':'2026-10-20','end_date':'2026-10-21'}, {'title':'Invalid date','latitude':'12','longitude':'77','start_date':'bogus','end_date':'2026-10-21'}])
def test_project_invalid_input(roads_client, app, data):
    assert roads_client.post('/workspace/projects/new',data=data).status_code==200
    with app.app_context():assert Project.query.filter_by(title=data['title']).count()==0


def test_upload_limits(app, tmp_path):
    from werkzeug.datastructures import FileStorage
    from app.uploads import save_image
    app.config['UPLOAD_FOLDER']=str(tmp_path/'proof')
    with app.app_context():
        with pytest.raises(BusinessRuleError):save_image(FileStorage(stream=io.BytesIO(b'x'*(5*1024*1024+1)),filename='large.png'))
        with pytest.raises(BusinessRuleError):save_image(FileStorage(stream=io.BytesIO(b''),filename='empty.png'))
    assert not (tmp_path/'proof').exists()


def test_private_proof_visibility(app, client, roads_client, tmp_path):
    from PIL import Image
    from werkzeug.datastructures import FileStorage
    from app.uploads import save_image
    app.config['UPLOAD_FOLDER']=str(tmp_path/'proof')
    with app.app_context():
        p=project_pair()[0];p.status='draft'
        stream=io.BytesIO();Image.new('RGB',(2,2)).save(stream,'PNG');stream.seek(0)
        url=save_image(FileStorage(stream=stream,filename='proof.png'))
        db.session.add(ProjectPhoto(project_id=p.id,photo_url=url,kind='general'));db.session.commit()
    assert client.get(url).status_code==404
    assert roads_client.get(url).status_code==200
    assert client.get('/media/not-a-safe-name.svg').status_code==404


def test_post_review_workflow(app, chairman_client, roads_client):
    from app.models import OfficialPost
    assert roads_client.post('/workspace/posts/new',data={'title':'Demo announcement','body':'Municipal demo notice','submit_now':'1'}).status_code==302
    with app.app_context():pid=OfficialPost.query.filter_by(title='Demo announcement').one().id
    assert chairman_client.post(f'/chairman/posts/{pid}/approve').status_code==302
    roads_client.post(f'/workspace/posts/{pid}',data={'action':'edit','body':'Forbidden correction'})
    with app.app_context():assert db.session.get(OfficialPost,pid).body=='Municipal demo notice'


def test_response_review_workflow(app):
    with app.app_context():
        p=project_pair()[0];cp=CitizenPost.query.first();chair=User.query.filter_by(role='chairman').one()
        response=services.respond_to_report('Demo response',p.creator,cp)
        services.approve_response(response,chair)
        assert response.status=='approved'
        with pytest.raises(BusinessRuleError):services.approve_response(response,chair)


def test_joint_invalid_post_returns_400(roads_client):
    assert roads_client.post('/workspace/joint-schedules/propose',data={'project_ids':'not-an-id','start_date':'bad'}).status_code==400


def test_department_duplicate_does_not_crash(admin_client, app):
    res=admin_client.post('/admin/departments',data={'name':'Roads','slug':'new-road'})
    assert res.status_code==200 and b'already exists' in res.data
    with app.app_context():assert Department.query.filter_by(slug='new-road').count()==0


def test_all_role_pages_render(client, citizen_client, roads_client, chairman_client, admin_client):
    cases=[(client,['/','/projects','/map','/departments','/coordination','/citizen-reports','/joint-schedules','/about']),
           (citizen_client,['/dashboard','/dashboard/reports','/dashboard/subscriptions','/dashboard/notifications','/profile']),
           (roads_client,['/workspace/','/workspace/projects','/workspace/projects/new','/workspace/posts','/workspace/posts/new','/workspace/coordination','/workspace/reports','/workspace/notifications']),
           (chairman_client,['/chairman/','/chairman/employees','/chairman/posts','/chairman/projects','/chairman/conflicts','/chairman/schedules','/chairman/audit-logs','/chairman/departments','/chairman/notifications']),
           (admin_client,['/admin/','/admin/departments','/admin/users','/admin/seed','/admin/system'])]
    for c,urls in cases:
        for url in urls:
            response=c.get(url,follow_redirects=True)
            assert response.status_code==200, url


def test_every_template_literal_endpoint_exists(app):
    import re
    from pathlib import Path
    for template in (Path(app.root_path)/'templates').rglob('*.html'):
        for endpoint in re.findall(r"url_for\(\s*['\"]([^'\"]+)['\"]",template.read_text(encoding='utf8')):
            assert endpoint in app.view_functions, (str(template),endpoint)


def test_delay_reason_requires_review(app):
    with app.app_context():
        p=project_pair()[0];p.status='delayed';p.progress_percentage=100
        post=services.post_delay_reason(p,p.creator,'Demo rainfall')
        assert p.delay_reason is None and post.status=='pending_chairman'
        chair=User.query.filter_by(role='chairman').one()
        services.approve_post(post,chair)
        assert p.delay_reason=='Demo rainfall'

#!/usr/bin/env python3
"""
Manual deployment trigger script
Usage: python trigger_deployment.py [deployment_id] [app_id]
"""
import sys
from celery_app import celery_app
from database import SessionLocal
from models import Deployment, App

def trigger_deployment(deployment_id: str = None, app_id: str = None):
    """Trigger a deployment task manually"""
    
    db = SessionLocal()
    
    try:
        # If no IDs provided, get the latest pending deployment
        if not deployment_id or not app_id:
            print("🔍 Looking for pending deployments...")
            deployment = db.query(Deployment).filter(
                Deployment.status == 'PENDING'
            ).order_by(Deployment.deploymentId.desc()).first()
            
            if not deployment:
                print("❌ No pending deployments found")
                print("\n📋 Available deployments:")
                all_deployments = db.query(Deployment).order_by(
                    Deployment.deploymentId.desc()
                ).limit(5).all()
                for d in all_deployments:
                    print(f"   - {d.name} ({d.status}) - ID: {d.deploymentId}")
                return
            
            deployment_id = str(deployment.deploymentId)
            app_id = str(deployment.appId)
            
            print(f"📦 Found deployment: {deployment.name}")
            print(f"   Deployment ID: {deployment_id}")
            print(f"   App ID: {app_id}")
        else:
            # Verify the deployment exists
            deployment = db.query(Deployment).filter(
                Deployment.deploymentId == deployment_id
            ).first()
            if not deployment:
                print(f"❌ Deployment {deployment_id} not found")
                return
        
        # Trigger the Celery task
        print(f"\n🚀 Triggering deployment task...")
        result = celery_app.send_task(
            'tasks.deploy_application',
            args=[deployment_id, app_id],
            queue='celery'
        )
        
        print(f"✅ Task sent successfully!")
        print(f"   Task ID: {result.id}")
        print(f"   Deployment: {deployment.name}")
        print(f"   Status: {deployment.status}")
        print(f"\n📊 Monitor the task:")
        print(f"   - Flower: http://localhost:5555")
        print(f"   - Task ID: {result.id}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) == 3:
        # Manual IDs provided
        trigger_deployment(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 1:
        # Auto-detect pending deployment
        trigger_deployment()
    else:
        print("Usage:")
        print("  python trigger_deployment.py                           # Auto-detect pending deployment")
        print("  python trigger_deployment.py <deployment_id> <app_id>  # Specific deployment")

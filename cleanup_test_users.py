import os
from app import create_app
from models import db, User

def cleanup():
    app = create_app()
    with app.app_context():
        print("Starting Naadan test users database cleanup...")
        
        # Test emails to delete
        test_emails = ["dummy@naadan.com", "9497856550@naadan.com"]
        
        deleted_count = 0
        for email in test_emails:
            users = User.query.filter_by(email=email).all()
            for user in users:
                print(f"Deleting user: ID={user.id}, Name={user.name}, Email={user.email}")
                # Delete any associated products or orders first to avoid foreign key errors
                try:
                    db.session.execute(db.text(f"DELETE FROM product WHERE farmer_id = {user.id}"))
                    db.session.execute(db.text(f"DELETE FROM \"order\" WHERE buyer_id = {user.id} OR farmer_id = {user.id}"))
                    db.session.execute(db.text(f"DELETE FROM rating WHERE buyer_id = {user.id} OR farmer_id = {user.id}"))
                    db.session.execute(db.text(f"DELETE FROM notification WHERE user_id = {user.id}"))
                    db.session.execute(db.text(f"DELETE FROM audit_event WHERE user_id = {user.id}"))
                except Exception as e:
                    print(f"Error clearing relations for user {user.id}: {e}")
                db.session.delete(user)
                deleted_count += 1
                
        if deleted_count > 0:
            db.session.commit()
            print(f"Successfully cleaned up {deleted_count} test users.")
        else:
            print("No matching test users found in the database.")

if __name__ == "__main__":
    cleanup()
